#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT / "data" / "interim" / "merge_batches"
OUTPUT_DIR = ROOT / "data" / "interim" / "merge_results"
PROGRESS_DIR = OUTPUT_DIR / ".progress"
ERROR_LOG_PATH = OUTPUT_DIR / "errors.log"
PROMPT_PATH = ROOT / "prompts" / "merge_batch_prompt.md"

DEFAULT_PROVIDER = "codex"
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"
DEFAULT_GLM_MODEL = "glm-4.7"
DEFAULT_GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

VALID_CATEGORIES = {"Agent", "RAG", "AICoding", "Prompt", "MCP", "CLI", "Evaluation", "LLM", "OpenEnded"}
VALID_DIFFICULTY = {"基础", "进阶", "深入"}
VALID_JOB_LEVEL = {"初级", "中级", "高级"}
BATCH_PATTERN = re.compile(r"^merge_batch_(\d{3})\.json$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run merge for merge batch JSON files.")
    parser.add_argument("--start", type=int, help="Start batch number, e.g. 1 for merge_batch_001.json.")
    parser.add_argument("--end", type=int, help="End batch number, e.g. 3 for merge_batch_003.json.")
    parser.add_argument("--all", action="store_true", help="Process all batch files under data/interim/merge_batches.")
    parser.add_argument("--dry-run", action="store_true", help="Print batches that would be processed without calling the model.")
    parser.add_argument("--sleep-seconds", type=float, default=1.0, help="Sleep duration between batches.")
    parser.add_argument("--chunk-size", type=int, default=5, help="Split each merge batch file into smaller sub-batches.")
    parser.add_argument("--max-retries", type=int, default=2, help="Max retries per sub-batch after the first attempt fails.")
    parser.add_argument("--timeout-seconds", type=float, default=180.0, help="Timeout per request.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    parser.add_argument("--provider", choices=("codex", "glm"), default=DEFAULT_PROVIDER, help="Model provider to use.")
    parser.add_argument("--model", default=None, help="Optional model override.")
    parser.add_argument("--codex-bin", default=None, help="Path to codex executable. Defaults to codex in PATH.")
    parser.add_argument("--glm-base-url", default=DEFAULT_GLM_BASE_URL, help="GLM API base URL.")
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


def load_prompt_template() -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    if "{{BATCH_JSON}}" not in template:
        raise ValueError(f"Prompt template missing {{BATCH_JSON}} placeholder: {PROMPT_PATH}")
    return template


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


def parse_batch_number(path: Path) -> int | None:
    match = BATCH_PATTERN.match(path.name)
    if not match:
        return None
    return int(match.group(1))


def select_batch_files(args: argparse.Namespace) -> list[Path]:
    all_batches = sorted(
        path for path in INPUT_DIR.glob("merge_batch_*.json")
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


def normalize_canonical_item(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"canonical_items[{index}] is not a JSON object")

    required_fields = ["canonical_question", "raw_ids", "category", "tags", "difficulty", "job_level"]
    missing = [field for field in required_fields if field not in item]
    if missing:
        raise ValueError(f"canonical_items[{index}] missing required fields: {', '.join(missing)}")

    canonical_question = str(item["canonical_question"]).strip()
    if not canonical_question:
        raise ValueError(f"canonical_items[{index}] canonical_question must be non-empty")

    raw_ids = normalize_string_list(item["raw_ids"], f"canonical_items[{index}].raw_ids")
    category = normalize_string_list(item["category"], f"canonical_items[{index}].category")
    invalid_categories = [value for value in category if value not in VALID_CATEGORIES]
    if invalid_categories:
        raise ValueError(f"canonical_items[{index}] invalid category values: {', '.join(invalid_categories)}")

    tags = normalize_string_list(item["tags"], f"canonical_items[{index}].tags")

    difficulty = str(item["difficulty"]).strip()
    if difficulty not in VALID_DIFFICULTY:
        raise ValueError(f"canonical_items[{index}] invalid difficulty: {difficulty}")

    job_level = normalize_string_list(item["job_level"], f"canonical_items[{index}].job_level")
    invalid_job_levels = [value for value in job_level if value not in VALID_JOB_LEVEL]
    if invalid_job_levels:
        raise ValueError(f"canonical_items[{index}] invalid job_level values: {', '.join(invalid_job_levels)}")

    return {
        "canonical_question": canonical_question,
        "raw_ids": raw_ids,
        "category": category,
        "tags": tags,
        "difficulty": difficulty,
        "job_level": job_level,
    }


def normalize_output_record(record: Any, index: int, expected_group: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError(f"Record {index} is not a JSON object")

    if "candidate_group_id" not in record or "canonical_items" not in record:
        raise ValueError(f"Record {index} must contain candidate_group_id and canonical_items")

    candidate_group_id = str(record["candidate_group_id"]).strip()
    expected_group_id = str(expected_group.get("candidate_group_id", "")).strip()
    if candidate_group_id != expected_group_id:
        raise ValueError(
            f"Record {index} candidate_group_id mismatch: expected {expected_group_id}, got {candidate_group_id}"
        )

    canonical_items_value = record["canonical_items"]
    if not isinstance(canonical_items_value, list):
        raise ValueError(f"Record {index} canonical_items must be a JSON array")

    source_questions = expected_group.get("questions")
    if not isinstance(source_questions, list):
        raise ValueError(f"Expected group {expected_group_id} missing questions array")
    valid_raw_ids = {
        str(question.get("raw_id", "")).strip()
        for question in source_questions
        if isinstance(question, dict) and str(question.get("raw_id", "")).strip()
    }

    normalized_canonical_items: list[dict[str, Any]] = []
    seen_raw_ids: set[str] = set()
    for item_idx, item in enumerate(canonical_items_value, start=1):
        normalized_item = normalize_canonical_item(item, item_idx)
        for raw_id in normalized_item["raw_ids"]:
            if raw_id not in valid_raw_ids:
                raise ValueError(
                    f"Record {index} canonical_items[{item_idx}] raw_id not in candidate group: {raw_id}"
                )
            if raw_id in seen_raw_ids:
                raise ValueError(
                    f"Record {index} canonical_items[{item_idx}] raw_id appears in multiple canonical_items: {raw_id}"
                )
            seen_raw_ids.add(raw_id)
        normalized_canonical_items.append(normalized_item)

    return {
        "candidate_group_id": candidate_group_id,
        "canonical_items": normalized_canonical_items,
    }


def validate_result_records(records: list[Any], expected_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(records) != len(expected_groups):
        raise ValueError(f"Output count {len(records)} does not match input count {len(expected_groups)}")

    normalized_records: list[dict[str, Any]] = []
    for idx, record in enumerate(records, start=1):
        normalized = normalize_output_record(record, idx, expected_groups[idx - 1])
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
            if not isinstance(record, dict):
                return False
            if "candidate_group_id" not in record or "canonical_items" not in record:
                return False
        return True
    except Exception:
        return False


def load_existing_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, list):
        return len(payload)
    return 0


def build_prompt(prompt_template: str, batch_records: list[dict[str, Any]]) -> str:
    return prompt_template.replace("{{BATCH_JSON}}", json.dumps(batch_records, ensure_ascii=False, indent=2))


def run_codex_exec(codex_bin: str, prompt: str, model: str, timeout_seconds: float) -> str:
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


def call_glm_api(prompt: str, api_key: str, base_url: str, model: str, timeout_seconds: float) -> str:
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": "You are a strict JSON merge assistant. Output only a JSON array."},
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
) -> str:
    if provider == "codex":
        if not codex_bin:
            raise ValueError("codex_bin is required when provider=codex")
        return run_codex_exec(codex_bin=codex_bin, prompt=prompt, model=model, timeout_seconds=timeout_seconds)
    if provider == "glm":
        if not glm_api_key or not glm_base_url:
            raise ValueError("GLM API key and base URL are required when provider=glm")
        return call_glm_api(prompt=prompt, api_key=glm_api_key, base_url=glm_base_url, model=model, timeout_seconds=timeout_seconds)
    raise ValueError(f"Unsupported provider: {provider}")


def get_retry_delay_seconds(exc: Exception, attempt: int) -> float:
    if isinstance(exc, urllib.error.HTTPError):
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 1.0)
            except ValueError:
                pass
        if exc.code == 429:
            return min(15.0 * (2 ** (attempt - 1)), 120.0)
    return min(2.0 ** (attempt - 1), 10.0)


def append_error_log(batch_name: str, chunk_label: str, error_message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with ERROR_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {batch_name} | {chunk_label} | {error_message}\n")


def write_result(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
        f.write("\n")


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
    print(f"输入 group 数: {input_count}")
    print(f"输出 group 数: {output_count}")
    print(f"耗时: {elapsed_seconds:.2f}s")
    print(f"是否成功: {status}")
    print("")


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
) -> bool:
    start_time = time.perf_counter()
    batch_name = batch_path.name
    result_path = result_path_for(batch_path)
    batch_records = load_json_array(batch_path)
    input_count = len(batch_records)

    if is_valid_existing_result(result_path):
        elapsed = time.perf_counter() - start_time
        print_batch_summary(batch_name, input_count, load_existing_count(result_path), elapsed, True, skipped=True)
        return True

    all_normalized_records: list[dict[str, Any]] = []
    sub_batches = chunk_records(batch_records, chunk_size)

    for sub_index, sub_batch in enumerate(sub_batches, start=1):
        sub_label = f"chunk {sub_index}/{len(sub_batches)}"
        last_error = ""

        for attempt in range(1, max_retries + 2):
            try:
                logging.info(
                    "Processing %s | %s | groups=%d | attempt %d/%d | provider=%s | model=%s",
                    batch_name,
                    sub_label,
                    len(sub_batch),
                    attempt,
                    max_retries + 1,
                    provider,
                    model,
                )
                prompt = build_prompt(prompt_template, sub_batch)
                raw_text = call_model(
                    provider=provider,
                    prompt=prompt,
                    model=model,
                    timeout_seconds=timeout_seconds,
                    codex_bin=codex_bin,
                    glm_api_key=glm_api_key,
                    glm_base_url=glm_base_url,
                )
                result_records = extract_json_array(raw_text)
                normalized_records = validate_result_records(result_records, sub_batch)
                all_normalized_records.extend(normalized_records)
                break
            except (
                subprocess.SubprocessError,
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
                json.JSONDecodeError,
                ValueError,
                RuntimeError,
            ) as exc:
                last_error = f"attempt {attempt} failed: {exc}"
                logging.warning("%s | %s | %s", batch_name, sub_label, last_error)
                if attempt <= max_retries:
                    delay_seconds = get_retry_delay_seconds(exc, attempt)
                    logging.info("Retrying %s | %s after %.1fs", batch_name, sub_label, delay_seconds)
                    time.sleep(delay_seconds)
        else:
            elapsed = time.perf_counter() - start_time
            append_error_log(batch_name, sub_label, last_error or "unknown error")
            print_batch_summary(batch_name, input_count, len(all_normalized_records), elapsed, False)
            return False

    write_result(result_path, all_normalized_records)
    elapsed = time.perf_counter() - start_time
    print_batch_summary(batch_name, input_count, len(all_normalized_records), elapsed, True)
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

    try:
        batch_files = select_batch_files(args)
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
        return 0

    try:
        prompt_template = load_prompt_template()
        if args.provider == "codex":
            model = args.model.strip() if isinstance(args.model, str) and args.model.strip() else DEFAULT_CODEX_MODEL
            codex_bin = get_codex_bin(args.codex_bin)
            glm_api_key = None
            glm_base_url = None
        else:
            model = args.model.strip() if isinstance(args.model, str) and args.model.strip() else get_env("GLM_MODEL")
            codex_bin = None
            glm_api_key = get_env("GLM_API_KEY")
            glm_base_url = normalize_base_url(get_env("GLM_BASE_URL"))
    except ValueError as exc:
        logging.error("%s", exc)
        return 1

    success_count = 0
    failure_count = 0

    for index, batch_path in enumerate(batch_files, start=1):
        try:
            ok = process_batch(
                batch_path=batch_path,
                prompt_template=prompt_template,
                provider=args.provider,
                model=model,
                chunk_size=args.chunk_size,
                max_retries=args.max_retries,
                timeout_seconds=args.timeout_seconds,
                codex_bin=codex_bin,
                glm_api_key=glm_api_key,
                glm_base_url=glm_base_url,
            )
        except Exception as exc:
            ok = False
            append_error_log(batch_path.name, "batch", str(exc))
            logging.exception("Unexpected failure while processing %s", batch_path.name)
            batch_records = load_json_array(batch_path)
            print_batch_summary(batch_path.name, len(batch_records), 0, 0.0, False)

        if ok:
            success_count += 1
        else:
            failure_count += 1

        if index < len(batch_files) and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    print(f"总批次数: {len(batch_files)}")
    print(f"成功批次: {success_count}")
    print(f"失败批次: {failure_count}")
    return 0 if failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
