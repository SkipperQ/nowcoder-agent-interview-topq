#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT / "data" / "interim" / "llm_batches"
OUTPUT_DIR = ROOT / "data" / "interim" / "llm_results"
ERROR_LOG_PATH = OUTPUT_DIR / "errors.log"

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

PROMPT_TEMPLATE = """你是一个面试题元数据标注助手。请对输入的 JSON 数组逐条标注，并且只输出 JSON 数组，不要输出任何解释、标题、markdown 或代码块。

输入数组中的每个元素都是一个待标注问题对象，至少包含：
- raw_id
- raw_question
- company
- department
- source
- source_title
- source_file
- context

你必须为每条输入输出一条结果，且输出数组长度必须与输入一致，顺序必须与输入一致。

每条结果字段必须完整且固定为：
1. raw_id
2. keep_status
3. drop_reason
4. normalized_question
5. category
6. tags
7. difficulty
8. job_level
9. confidence

字段规则：
1. raw_id：必须与输入原样一致。
2. keep_status：只能是 keep / rewrite / drop。
3. drop_reason：
   - 如果 keep_status 是 drop，只能填写 non_ai 或 vague。
   - 如果 keep_status 不是 drop，必须填写空字符串 ""。
4. normalized_question：
   - 使用中文。
   - 改写为适合面试题库收录的一句话标准问题。
   - 如果 keep_status=drop 但仍能判断原意，尽量给出可读版本；完全无法判断时填 ""。
5. category：
   - 必须是 JSON 数组。
   - 可多选，但只能从以下枚举中选择：Agent、RAG、AICoding、Prompt、MCP、CLI、Evaluation、LLM、OpenEnded。
   - 至少 1 项，按核心考点优先，不要发明新类别。
6. tags：
   - 必须是 JSON 数组。
   - 使用英文小写下划线。
   - 建议 1 到 4 个，不要发明明显无关标签。
   - 可参考：query_rewrite, intent_recognition, chunking, rerank, tool_calling, mcp_auth, model_routing, context_engineering, llm_judge, dpo, sft。
7. difficulty：只能是 基础 / 进阶 / 深入。
8. job_level：
   - 必须是 JSON 数组。
   - 可多选，但只能从以下枚举中选择：初级、中级、高级。
   - 至少 1 项。
9. confidence：0 到 1 之间的数字。

判定规则：
- 非 AI / Agent / RAG / LLM / MCP / AICoding 相关问题，标记 keep_status=drop, drop_reason=non_ai。
- 太依赖上下文、无法形成通用面试题的问题，标记 keep_status=drop, drop_reason=vague。
- AI 工程化问题要保留，例如：LLM 流式响应限流、Agent 工具调用超时、RAG 检索缓存、MCP 工具调用失败降级、向量检索服务稳定性。

输出要求：
- 只输出 JSON 数组。
- 不要输出 ```json 或 ```。
- 不要漏字段。
- 不要输出注释。

下面是待标注输入：
{batch_json}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run GLM labeling for llm batch JSON files.")
    parser.add_argument("--start", type=int, help="Start batch number, e.g. 1 for batch_001.json.")
    parser.add_argument("--end", type=int, help="End batch number, e.g. 3 for batch_003.json.")
    parser.add_argument("--all", action="store_true", help="Process all batch files under data/interim/llm_batches.")
    parser.add_argument("--dry-run", action="store_true", help="Print batches that would be processed without calling the API.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Sleep duration between batches.")
    parser.add_argument("--chunk-size", type=int, default=8, help="Split each batch file into smaller API sub-batches.")
    parser.add_argument("--max-retries", type=int, default=2, help="Max retries per batch after the first attempt fails.")
    parser.add_argument("--timeout-seconds", type=float, default=180.0, help="HTTP timeout per request.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def ensure_directories() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v4"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/chat/completions"


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
    stem = batch_path.stem
    return OUTPUT_DIR / f"{stem}_result.json"


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


def build_messages(batch_records: list[dict[str, Any]]) -> list[dict[str, str]]:
    prompt = PROMPT_TEMPLATE.format(
        batch_json=json.dumps(batch_records, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": "你是一个严格输出 JSON 的面试题标注助手。"},
        {"role": "user", "content": prompt},
    ]


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
    batch_records: list[dict[str, Any]],
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float,
) -> str:
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": build_messages(batch_records),
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


def append_error_log(batch_name: str, error_message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with ERROR_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {batch_name} | {error_message}\n")


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
    print(f"输入条数: {input_count}")
    print(f"输出条数: {output_count}")
    print(f"耗时: {elapsed_seconds:.2f}s")
    print(f"是否成功: {status}")
    print("")


def process_batch(
    batch_path: Path,
    api_key: str,
    base_url: str,
    model: str,
    chunk_size: int,
    max_retries: int,
    timeout_seconds: float,
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

    expected_raw_ids = []
    for idx, record in enumerate(batch_records, start=1):
        raw_id = record.get("raw_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError(f"{batch_name} input record {idx} missing valid raw_id")
        expected_raw_ids.append(raw_id.strip())

    all_normalized_records: list[dict[str, Any]] = []
    sub_batches = chunk_records(batch_records, chunk_size)

    for sub_index, sub_batch in enumerate(sub_batches, start=1):
        sub_expected_raw_ids = [str(record["raw_id"]).strip() for record in sub_batch]
        sub_label = f"{batch_name} chunk {sub_index}/{len(sub_batches)}"
        last_error = ""

        for attempt in range(1, max_retries + 2):
            try:
                logging.info(
                    "Processing %s | records=%d | attempt %d/%d | model=%s | timeout=%.1fs",
                    sub_label,
                    len(sub_batch),
                    attempt,
                    max_retries + 1,
                    model,
                    timeout_seconds,
                )
                raw_text = call_glm_api(
                    batch_records=sub_batch,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout_seconds=timeout_seconds,
                )
                result_records = extract_json_array(raw_text)
                normalized_records = validate_result_records(result_records, sub_expected_raw_ids)
                all_normalized_records.extend(normalized_records)
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                last_error = f"attempt {attempt} failed: {exc}"
                logging.warning("%s | %s", sub_label, last_error)
                if attempt <= max_retries:
                    delay_seconds = get_retry_delay_seconds(exc, attempt)
                    logging.info("Retrying %s after %.1fs", sub_label, delay_seconds)
                    time.sleep(delay_seconds)
        else:
            elapsed = time.perf_counter() - start_time
            append_error_log(sub_label, last_error or "unknown error")
            print_batch_summary(batch_name, input_count, len(all_normalized_records), elapsed, False)
            return False

    write_result(result_path, all_normalized_records)
    elapsed = time.perf_counter() - start_time
    print_batch_summary(batch_name, input_count, len(all_normalized_records), elapsed, True)
    return True


def load_existing_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, list):
        return len(payload)
    return 0


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
        api_key = get_env("GLM_API_KEY")
        base_url = normalize_base_url(get_env("GLM_BASE_URL"))
        model = get_env("GLM_MODEL")
    except ValueError as exc:
        logging.error("%s", exc)
        return 1

    success_count = 0
    failure_count = 0

    for index, batch_path in enumerate(batch_files, start=1):
        try:
            ok = process_batch(
                batch_path=batch_path,
                api_key=api_key,
                base_url=base_url,
                model=model,
                chunk_size=args.chunk_size,
                max_retries=args.max_retries,
                timeout_seconds=args.timeout_seconds,
            )
        except Exception as exc:
            ok = False
            append_error_log(batch_path.name, str(exc))
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
