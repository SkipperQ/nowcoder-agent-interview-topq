#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT / "data" / "interim" / "llm_batches"
OUTPUT_DIR = ROOT / "data" / "interim" / "llm_results"
PROGRESS_DIR = OUTPUT_DIR / ".progress"
ERROR_LOG_PATH = OUTPUT_DIR / "errors.log"
PROMPT_PATH = ROOT / "prompts" / "label_batch_prompt.md"

REQUIRED_OUTPUT_FIELDS = [
    "raw_id",
    "keep_status",
    "drop_reason",
    "normalized_question",
    "category",
    "tags",
    "difficulty",
    "job_level",
    "confidence",
]

VALID_KEEP_STATUS = {"keep", "rewrite", "drop"}
VALID_CATEGORIES = {"Agent", "RAG", "AICoding", "Prompt", "MCP", "CLI", "Evaluation", "LLM", "OpenEnded"}
VALID_DIFFICULTY = {"基础", "进阶", "深入"}
VALID_JOB_LEVEL = {"初级", "中级", "高级"}
VALID_DROP_REASONS = {"", "non_ai", "vague"}
TAG_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
BATCH_PATTERN = re.compile(r"^batch_(\d{3})\.json$")

DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_GLM_MODEL = "glm-4.7"
DEFAULT_GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
GLM_REQUEST_SEMAPHORE = threading.Semaphore(1)
GLM_REQUEST_LOCK = threading.Lock()
GLM_NEXT_REQUEST_TS = 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Codex gpt-5.4-mini labeling for llm batch JSON files.")
    parser.add_argument("--start", type=int, help="Start batch number, e.g. 1 for batch_001.json.")
    parser.add_argument("--end", type=int, help="End batch number, e.g. 3 for batch_003.json.")
    parser.add_argument("--all", action="store_true", help="Process all batch files under data/interim/llm_batches.")
    parser.add_argument("--dry-run", action="store_true", help="Print batches that would be processed without calling Codex.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Sleep duration between batches.")
    parser.add_argument("--chunk-size", type=int, default=8, help="Split each batch file into smaller Codex sub-batches.")
    parser.add_argument("--max-retries", type=int, default=2, help="Max retries per sub-batch after the first attempt fails.")
    parser.add_argument("--timeout-seconds", type=float, default=180.0, help="Timeout per Codex exec request.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    parser.add_argument("--provider", choices=("codex", "glm", "hybrid"), default="codex", help="Model provider to use.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Primary model name.")
    parser.add_argument("--fallback-model", default=DEFAULT_MODEL, help="Fallback Codex model name for provider=hybrid.")
    parser.add_argument("--codex-bin", default=None, help="Path to codex executable. Defaults to codex in PATH.")
    parser.add_argument("--glm-base-url", default=DEFAULT_GLM_BASE_URL, help="GLM API base URL.")
    parser.add_argument("--glm-max-concurrent", type=int, default=1, help="Max concurrent GLM requests across all worker threads.")
    parser.add_argument("--glm-min-interval-seconds", type=float, default=1.5, help="Minimum interval between GLM requests.")
    parser.add_argument("--glm-cooldown-seconds", type=float, default=600.0, help="Extra cooldown after GLM 429 before the next GLM request.")
    parser.add_argument("--jobs", type=int, default=1, help="Number of batch files to process in parallel.")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def ensure_directories() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_prompt_template() -> str:
    with PROMPT_PATH.open("r", encoding="utf-8") as f:
        template = f.read()
    if "{{BATCH_JSON}}" not in template:
        raise ValueError(f"Prompt template missing {{BATCH_JSON}} placeholder: {PROMPT_PATH}")
    return template


def get_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def get_codex_bin(explicit_path: str | None) -> str:
    if explicit_path:
        return explicit_path
    resolved = shutil.which("codex")
    if not resolved:
        raise ValueError("Could not find codex executable in PATH")
    return resolved


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v4"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/chat/completions"


def configure_glm_rate_limit(max_concurrent: int, min_interval_seconds: float) -> None:
    global GLM_REQUEST_SEMAPHORE, GLM_NEXT_REQUEST_TS
    if max_concurrent <= 0:
        raise ValueError("--glm-max-concurrent must be > 0")
    if min_interval_seconds < 0:
        raise ValueError("--glm-min-interval-seconds must be >= 0")
    GLM_REQUEST_SEMAPHORE = threading.Semaphore(max_concurrent)
    with GLM_REQUEST_LOCK:
        GLM_NEXT_REQUEST_TS = 0.0


def throttle_glm_request(min_interval_seconds: float) -> None:
    global GLM_NEXT_REQUEST_TS
    with GLM_REQUEST_LOCK:
        now = time.monotonic()
        if now < GLM_NEXT_REQUEST_TS:
            time.sleep(GLM_NEXT_REQUEST_TS - now)
            now = time.monotonic()
        GLM_NEXT_REQUEST_TS = now + min_interval_seconds


def apply_glm_cooldown(cooldown_seconds: float) -> None:
    global GLM_NEXT_REQUEST_TS
    if cooldown_seconds <= 0:
        return
    with GLM_REQUEST_LOCK:
        GLM_NEXT_REQUEST_TS = max(GLM_NEXT_REQUEST_TS, time.monotonic() + cooldown_seconds)


def load_json_array(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError(f"{path} is not a JSON array")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"{path} contains non-object items")
    return payload


def chunk_records(records: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [records[i:i + chunk_size] for i in range(0, len(records), chunk_size)]


def build_model_input_records(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    slim_records: list[dict[str, str]] = []
    for index, record in enumerate(records, start=1):
        raw_id = record.get("raw_id")
        raw_question = record.get("raw_question")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError(f"Input record {index} missing valid raw_id")
        if not isinstance(raw_question, str) or not raw_question.strip():
            raise ValueError(f"Input record {index} missing valid raw_question")

        slim_records.append(
            {
                "raw_id": raw_id.strip(),
                "raw_question": raw_question.strip(),
                "company": str(record.get("company", "")).strip(),
                "department": str(record.get("department", "")).strip(),
                "source": str(record.get("source", "")).strip(),
                "source_title": str(record.get("source_title", "")).strip(),
                "context": str(record.get("context", "")).strip(),
            }
        )
    return slim_records


def parse_batch_number(path: Path) -> int | None:
    match = BATCH_PATTERN.match(path.name)
    if not match:
        return None
    return int(match.group(1))


def select_batch_files(args: argparse.Namespace) -> list[Path]:
    all_batches = sorted(
        path for path in INPUT_DIR.glob("batch_*.json")
        if parse_batch_number(path) is not None
    )

    if args.all:
        return all_batches

    if args.start is None and args.end is None:
        raise ValueError("Must provide either --all or a --start/--end range")

    start = args.start if args.start is not None else args.end
    end = args.end if args.end is not None else args.start
    if start is None or end is None:
        raise ValueError("Invalid range selection")
    if start <= 0 or end <= 0:
        raise ValueError("--start and --end must be positive integers")
    if start > end:
        raise ValueError("--start cannot be greater than --end")

    selected: list[Path] = []
    for path in all_batches:
        batch_no = parse_batch_number(path)
        if batch_no is not None and start <= batch_no <= end:
            selected.append(path)
    return selected


def result_path_for(batch_path: Path) -> Path:
    return OUTPUT_DIR / f"{batch_path.stem}_result.json"


def progress_path_for(batch_name: str, chunk_index: int) -> Path:
    return PROGRESS_DIR / f"{Path(batch_name).stem}.chunk_{chunk_index:02d}.json"


def extract_json_array(text: str) -> list[Any]:
    cleaned = text.strip()
    cleaned = cleaned.replace("```json", "").replace("```JSON", "").replace("```", "").strip()

    decoder = json.JSONDecoder()
    for idx, char in enumerate(cleaned):
        if char != "[":
            continue
        try:
            payload, _ = decoder.raw_decode(cleaned[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            return payload
    raise ValueError("Could not extract a JSON array from model output")


def normalize_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON array")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} items must be strings")
        stripped = item.strip()
        if not stripped:
            raise ValueError(f"{field_name} cannot contain empty strings")
        normalized.append(stripped)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} cannot contain duplicates")
    return normalized


def normalize_output_record(record: Any, index: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError(f"Record {index} is not a JSON object")

    missing = [field for field in REQUIRED_OUTPUT_FIELDS if field not in record]
    if missing:
        raise ValueError(f"Record {index} missing required fields: {', '.join(missing)}")

    normalized: dict[str, Any] = {}
    for field in REQUIRED_OUTPUT_FIELDS:
        normalized[field] = record[field]

    raw_id = normalized["raw_id"]
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError(f"Record {index} has invalid raw_id")
    normalized["raw_id"] = raw_id.strip()

    keep_status = normalized["keep_status"]
    if keep_status not in VALID_KEEP_STATUS:
        raise ValueError(f"Record {index} has invalid keep_status: {keep_status}")

    drop_reason = normalized["drop_reason"]
    if not isinstance(drop_reason, str):
        raise ValueError(f"Record {index} drop_reason must be a string")
    drop_reason = drop_reason.strip()
    if keep_status == "drop":
        if drop_reason not in {"non_ai", "vague"}:
            raise ValueError(f"Record {index} drop_reason must be non_ai or vague when keep_status=drop")
    else:
        if drop_reason != "":
            raise ValueError(f"Record {index} drop_reason must be empty when keep_status is not drop")
    normalized["drop_reason"] = drop_reason

    normalized_question = normalized["normalized_question"]
    if not isinstance(normalized_question, str):
        raise ValueError(f"Record {index} normalized_question must be a string")
    normalized["normalized_question"] = normalized_question.strip()

    categories = normalize_string_list(normalized["category"], "category")
    invalid_categories = [item for item in categories if item not in VALID_CATEGORIES]
    if invalid_categories:
        raise ValueError(f"Record {index} has invalid category values: {', '.join(invalid_categories)}")
    normalized["category"] = categories

    tags = normalize_string_list(normalized["tags"], "tags")
    invalid_tags = [item for item in tags if not TAG_PATTERN.fullmatch(item)]
    if invalid_tags:
        raise ValueError(f"Record {index} has invalid tag values: {', '.join(invalid_tags)}")
    normalized["tags"] = tags

    difficulty = normalized["difficulty"]
    if difficulty not in VALID_DIFFICULTY:
        raise ValueError(f"Record {index} has invalid difficulty: {difficulty}")

    job_levels = normalize_string_list(normalized["job_level"], "job_level")
    invalid_job_levels = [item for item in job_levels if item not in VALID_JOB_LEVEL]
    if invalid_job_levels:
        raise ValueError(f"Record {index} has invalid job_level values: {', '.join(invalid_job_levels)}")
    normalized["job_level"] = job_levels

    confidence = normalized["confidence"]
    if not isinstance(confidence, (int, float)):
        raise ValueError(f"Record {index} confidence must be numeric")
    confidence_value = float(confidence)
    if not 0.0 <= confidence_value <= 1.0:
        raise ValueError(f"Record {index} confidence must be between 0 and 1")
    normalized["confidence"] = round(confidence_value, 4)

    return normalized


def validate_result_records(records: list[Any], expected_raw_ids: list[str]) -> list[dict[str, Any]]:
    if len(records) != len(expected_raw_ids):
        raise ValueError(f"Output count {len(records)} does not match input count {len(expected_raw_ids)}")

    normalized_records: list[dict[str, Any]] = []
    for idx, record in enumerate(records, start=1):
        normalized = normalize_output_record(record, idx)
        expected_raw_id = expected_raw_ids[idx - 1]
        if normalized["raw_id"] != expected_raw_id:
            raise ValueError(
                f"Record {idx} raw_id mismatch: expected {expected_raw_id}, got {normalized['raw_id']}"
            )
        normalized_records.append(normalized)
    return normalized_records


def is_valid_existing_result(result_path: Path) -> bool:
    if not result_path.exists():
        return False
    try:
        with result_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, list):
            return False
        for idx, record in enumerate(payload, start=1):
            normalize_output_record(record, idx)
        return True
    except Exception:
        return False


def is_valid_progress_file(progress_path: Path, expected_raw_ids: list[str]) -> bool:
    if not progress_path.exists():
        return False
    try:
        with progress_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, list):
            return False
        validate_result_records(payload, expected_raw_ids)
        return True
    except Exception:
        return False


def validate_result_file(result_path: Path, expected_raw_ids: list[str]) -> list[dict[str, Any]]:
    with result_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError(f"{result_path.name} is not a JSON array")
    return validate_result_records(payload, expected_raw_ids)


def build_prompt(template: str, batch_records: list[dict[str, Any]]) -> str:
    model_input_records = build_model_input_records(batch_records)
    return template.replace("{{BATCH_JSON}}", json.dumps(model_input_records, ensure_ascii=False, indent=2))


def write_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def append_error_log(batch_name: str, chunk_label: str, error_message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with ERROR_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {batch_name} | {chunk_label} | {error_message}\n")


def print_batch_summary(
    batch_name: str,
    input_count: int,
    output_count: int,
    elapsed_seconds: float,
    success: bool,
    skipped: bool = False,
) -> None:
    status = "skipped" if skipped else ("success" if success else "failed")
    print(f"batch 文件名: {batch_name}")
    print(f"输入条数: {input_count}")
    print(f"输出条数: {output_count}")
    print(f"耗时: {elapsed_seconds:.2f}s")
    print(f"是否成功: {status}")
    print("")


def run_codex_exec(
    codex_bin: str,
    prompt: str,
    model: str,
    timeout_seconds: float,
) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        prefix="codex_last_message_",
        dir=PROGRESS_DIR,
        delete=False,
    ) as tmp:
        output_last_message = Path(tmp.name)

    command = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--model",
        model,
        "--sandbox",
        "read-only",
        "--output-last-message",
        str(output_last_message),
        "--cd",
        str(ROOT),
        "-",
    ]

    try:
        completed = subprocess.run(
            command,
            input=prompt.encode("utf-8"),
            text=False,
            capture_output=True,
            cwd=ROOT,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output_last_message.unlink(missing_ok=True)
        raise TimeoutError(f"codex exec timed out after {timeout_seconds:.1f}s") from exc
    except Exception:
        output_last_message.unlink(missing_ok=True)
        raise

    stdout = (completed.stdout or b"").decode("utf-8", errors="replace")
    stderr = (completed.stderr or b"").decode("utf-8", errors="replace")

    try:
        if completed.returncode != 0:
            raise RuntimeError(
                "codex exec failed with exit code "
                f"{completed.returncode}. stdout={stdout.strip()!r} stderr={stderr.strip()!r}"
            )
        if not output_last_message.exists():
            raise RuntimeError(
                f"codex exec did not write last message file. stdout={stdout.strip()!r} stderr={stderr.strip()!r}"
            )
        response_text = output_last_message.read_text(encoding="utf-8")
        if not response_text.strip():
            raise RuntimeError(
                f"codex exec returned an empty last message. stdout={stdout.strip()!r} stderr={stderr.strip()!r}"
            )
        return response_text
    finally:
        output_last_message.unlink(missing_ok=True)


def read_message_content(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: list[str] = []
        for item in message:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)
    raise ValueError("Model response does not contain text content")


def call_glm_api(
    prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float,
    min_interval_seconds: float,
) -> str:
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": "You are a strict JSON labeling assistant. Output only a JSON array."},
            {"role": "user", "content": prompt},
        ],
    }
    request = urllib.request.Request(
        url=base_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with GLM_REQUEST_SEMAPHORE:
        throttle_glm_request(min_interval_seconds)
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")

    response_json = json.loads(raw_body)
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("GLM response missing choices")

    message = choices[0].get("message")
    if not isinstance(message, dict) or "content" not in message:
        raise ValueError("GLM response missing message.content")
    return read_message_content(message["content"])


def call_model(
    provider: str,
    prompt: str,
    model: str,
    timeout_seconds: float,
    codex_bin: str | None = None,
    glm_api_key: str | None = None,
    glm_base_url: str | None = None,
    glm_min_interval_seconds: float = 0.0,
) -> str:
    if provider == "codex":
        if not codex_bin:
            raise ValueError("codex_bin is required when provider=codex")
        return run_codex_exec(
            codex_bin=codex_bin,
            prompt=prompt,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    if provider == "glm":
        if not glm_api_key or not glm_base_url:
            raise ValueError("GLM API key and base URL are required when provider=glm")
        return call_glm_api(
            prompt=prompt,
            api_key=glm_api_key,
            base_url=glm_base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            min_interval_seconds=glm_min_interval_seconds,
        )
    raise ValueError(f"Unsupported provider: {provider}")


def should_fallback_to_codex(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 409, 429, 500, 502, 503, 504}
    return isinstance(
        exc,
        (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            ValueError,
            RuntimeError,
        ),
    )


def get_retry_delay_seconds(exc: Exception, attempt: int) -> float:
    if isinstance(exc, urllib.error.HTTPError):
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 1.0)
            except ValueError:
                pass
        if exc.code == 429:
            return min(30.0 * (2 ** (attempt - 1)), 300.0)
    if isinstance(exc, urllib.error.URLError):
        return min(5.0 * (2 ** (attempt - 1)), 60.0)
    return min(2.0 ** (attempt - 1), 10.0)


def process_sub_batch(
    batch_name: str,
    chunk_index: int,
    total_chunks: int,
    sub_batch: list[dict[str, Any]],
    expected_raw_ids: list[str],
    prompt_template: str,
    provider: str,
    model: str,
    max_retries: int,
    timeout_seconds: float,
    codex_bin: str | None = None,
    glm_api_key: str | None = None,
    glm_base_url: str | None = None,
    glm_min_interval_seconds: float = 0.0,
    fallback_model: str | None = None,
    glm_cooldown_seconds: float = 0.0,
) -> tuple[bool, list[dict[str, Any]] | None]:
    progress_path = progress_path_for(batch_name, chunk_index)
    chunk_label = f"chunk {chunk_index}/{total_chunks}"

    if is_valid_progress_file(progress_path, expected_raw_ids):
        with progress_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        normalized = validate_result_records(payload, expected_raw_ids)
        logging.info("%s | reused cached progress: %s", batch_name, chunk_label)
        return True, normalized

    last_error = ""

    def run_single_provider(active_provider: str, active_model: str, attempts: int) -> tuple[bool, list[dict[str, Any]] | None, Exception | None]:
        nonlocal last_error
        for attempt in range(1, attempts + 1):
            try:
                logging.info(
                    "Processing %s | %s | records=%d | attempt %d/%d | provider=%s | model=%s",
                    batch_name,
                    chunk_label,
                    len(sub_batch),
                    attempt,
                    attempts,
                    active_provider,
                    active_model,
                )
                prompt = build_prompt(prompt_template, sub_batch)
                raw_text = call_model(
                    provider=active_provider,
                    prompt=prompt,
                    model=active_model,
                    timeout_seconds=timeout_seconds,
                    codex_bin=codex_bin,
                    glm_api_key=glm_api_key,
                    glm_base_url=glm_base_url,
                    glm_min_interval_seconds=glm_min_interval_seconds,
                )
                result_records = extract_json_array(raw_text)
                normalized_records = validate_result_records(result_records, expected_raw_ids)
                write_json(progress_path, normalized_records)
                return True, normalized_records, None
            except (
                subprocess.SubprocessError,
                TimeoutError,
                json.JSONDecodeError,
                ValueError,
                RuntimeError,
                urllib.error.HTTPError,
                urllib.error.URLError,
            ) as exc:
                last_error = f"attempt {attempt} failed: {exc}"
                logging.warning(
                    "%s | %s | provider=%s | model=%s | %s",
                    batch_name,
                    chunk_label,
                    active_provider,
                    active_model,
                    last_error,
                )
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                    apply_glm_cooldown(glm_cooldown_seconds)
                if attempt < attempts:
                    delay_seconds = get_retry_delay_seconds(exc, attempt)
                    logging.info(
                        "Retrying %s | %s | provider=%s after %.1fs",
                        batch_name,
                        chunk_label,
                        active_provider,
                        delay_seconds,
                    )
                    time.sleep(delay_seconds)
                else:
                    return False, None, exc
        return False, None, RuntimeError("unexpected retry flow")

    if provider == "hybrid":
        ok, normalized_records, exc = run_single_provider("glm", model, max_retries + 1)
        if ok:
            return True, normalized_records
        if exc is not None and fallback_model and should_fallback_to_codex(exc):
            logging.info(
                "%s | %s | switching to codex fallback | model=%s",
                batch_name,
                chunk_label,
                fallback_model,
            )
            ok, normalized_records, _ = run_single_provider("codex", fallback_model, 1)
            if ok:
                return True, normalized_records
    else:
        ok, normalized_records, _ = run_single_provider(provider, model, max_retries + 1)
        if ok:
            return True, normalized_records

    append_error_log(batch_name, chunk_label, last_error or "unknown error")
    return False, None


def process_batch(
    batch_path: Path,
    prompt_template: str,
    provider: str,
    model: str,
    chunk_size: int,
    max_retries: int,
    timeout_seconds: float,
    codex_bin: str | None = None,
    glm_api_key: str | None = None,
    glm_base_url: str | None = None,
    glm_min_interval_seconds: float = 0.0,
    fallback_model: str | None = None,
    glm_cooldown_seconds: float = 0.0,
) -> bool:
    start_time = time.perf_counter()
    batch_name = batch_path.name
    result_path = result_path_for(batch_path)
    batch_records = load_json_array(batch_path)
    input_count = len(batch_records)

    if is_valid_existing_result(result_path):
        elapsed = time.perf_counter() - start_time
        with result_path.open("r", encoding="utf-8") as f:
            existing = json.load(f)
        print_batch_summary(batch_name, input_count, len(existing), elapsed, True, skipped=True)
        return True

    expected_raw_ids: list[str] = []
    for idx, record in enumerate(batch_records, start=1):
        raw_id = record.get("raw_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError(f"{batch_name} input record {idx} missing valid raw_id")
        expected_raw_ids.append(raw_id.strip())

    sub_batches = chunk_records(batch_records, chunk_size)
    collected_records: list[dict[str, Any]] = []
    failed_chunks: list[int] = []

    for sub_index, sub_batch in enumerate(sub_batches, start=1):
        sub_expected_raw_ids = [str(record["raw_id"]).strip() for record in sub_batch]
        progress_path = progress_path_for(batch_name, sub_index)

        if is_valid_progress_file(progress_path, sub_expected_raw_ids):
            with progress_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            collected_records.extend(validate_result_records(payload, sub_expected_raw_ids))
            continue

        ok, normalized_records = process_sub_batch(
            batch_name=batch_name,
            chunk_index=sub_index,
            total_chunks=len(sub_batches),
            sub_batch=sub_batch,
            expected_raw_ids=sub_expected_raw_ids,
            prompt_template=prompt_template,
            provider=provider,
            model=model,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            codex_bin=codex_bin,
            glm_api_key=glm_api_key,
            glm_base_url=glm_base_url,
            glm_min_interval_seconds=glm_min_interval_seconds,
            fallback_model=fallback_model,
            glm_cooldown_seconds=glm_cooldown_seconds,
        )
        if not ok or normalized_records is None:
            failed_chunks.append(sub_index)
            continue
        collected_records.extend(normalized_records)

    if failed_chunks:
        elapsed = time.perf_counter() - start_time
        append_error_log(batch_name, "batch", f"incomplete after failed chunks: {', '.join(map(str, failed_chunks))}")
        print_batch_summary(batch_name, input_count, len(collected_records), elapsed, False)
        return False

    merged_records = validate_result_records(collected_records, expected_raw_ids)
    write_json(result_path, merged_records)

    try:
        validate_result_file(result_path, expected_raw_ids)
    except Exception as exc:
        append_error_log(batch_name, "validator", f"result validation failed: {exc}")
        elapsed = time.perf_counter() - start_time
        print_batch_summary(batch_name, input_count, len(merged_records), elapsed, False)
        return False

    elapsed = time.perf_counter() - start_time
    print_batch_summary(batch_name, input_count, len(merged_records), elapsed, True)
    return True


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)
    ensure_directories()

    if args.max_retries < 0:
        logging.error("--max-retries must be >= 0")
        return 1
    if args.chunk_size <= 0:
        logging.error("--chunk-size must be > 0")
        return 1
    if args.sleep_seconds < 0:
        logging.error("--sleep-seconds must be >= 0")
        return 1
    if args.timeout_seconds <= 0:
        logging.error("--timeout-seconds must be > 0")
        return 1
    if args.jobs <= 0:
        logging.error("--jobs must be > 0")
        return 1
    if args.provider in {"glm", "hybrid"}:
        try:
            configure_glm_rate_limit(args.glm_max_concurrent, args.glm_min_interval_seconds)
        except ValueError as exc:
            logging.error("%s", exc)
            return 1

    try:
        batch_files = select_batch_files(args)
        prompt_template = load_prompt_template()
        codex_bin = get_codex_bin(args.codex_bin) if args.provider in {"codex", "hybrid"} else None
        glm_api_key = get_env("GLM_API_KEY") if args.provider in {"glm", "hybrid"} else None
        glm_base_url = normalize_base_url(args.glm_base_url) if args.provider in {"glm", "hybrid"} else None
    except ValueError as exc:
        logging.error("%s", exc)
        return 1

    if not batch_files:
        logging.warning("No batch files matched the selection under %s", INPUT_DIR)
        return 0

    print("待处理 batch:")
    for path in batch_files:
        print(f"- {path.name}")
    print("")

    if args.dry_run:
        for path in batch_files:
            batch_records = load_json_array(path)
            sub_batches = chunk_records(batch_records, args.chunk_size)
            print(f"{path.name}: {len(batch_records)} 条, {len(sub_batches)} 个子批次, chunk_size={args.chunk_size}")
        return 0

    success_count = 0
    failure_count = 0
    batch_results: dict[str, bool] = {}

    def run_one_batch(batch_path: Path) -> tuple[str, bool]:
        try:
            ok = process_batch(
                batch_path=batch_path,
                prompt_template=prompt_template,
                provider=args.provider,
                model=args.model,
                chunk_size=args.chunk_size,
                max_retries=args.max_retries,
                timeout_seconds=args.timeout_seconds,
                codex_bin=codex_bin,
                glm_api_key=glm_api_key,
                glm_base_url=glm_base_url,
                glm_min_interval_seconds=args.glm_min_interval_seconds,
                fallback_model=args.fallback_model,
                glm_cooldown_seconds=args.glm_cooldown_seconds,
            )
            return batch_path.name, ok
        except Exception as exc:
            append_error_log(batch_path.name, "batch", str(exc))
            logging.exception("Unexpected failure while processing %s", batch_path.name)
            batch_records = load_json_array(batch_path)
            print_batch_summary(batch_path.name, len(batch_records), 0, 0.0, False)
            return batch_path.name, False

    if args.jobs == 1:
        for index, batch_path in enumerate(batch_files, start=1):
            batch_name, ok = run_one_batch(batch_path)
            batch_results[batch_name] = ok
            if ok:
                success_count += 1
            else:
                failure_count += 1
            if index < len(batch_files) and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            future_map = {executor.submit(run_one_batch, batch_path): batch_path for batch_path in batch_files}
            for future in as_completed(future_map):
                batch_name, ok = future.result()
                batch_results[batch_name] = ok
                if ok:
                    success_count += 1
                else:
                    failure_count += 1

        if args.sleep_seconds > 0:
            logging.info("sleep-seconds is ignored when jobs > 1")

    print(f"总批次数: {len(batch_files)}")
    print(f"成功批次: {success_count}")
    print(f"失败批次: {failure_count}")
    return 0 if failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
