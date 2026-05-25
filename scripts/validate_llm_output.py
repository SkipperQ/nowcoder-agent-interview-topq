#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT / "data" / "interim" / "llm_results"
REPORT_PATH = ROOT / "data" / "interim" / "validation_report.txt"

REQUIRED_FIELDS = [
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
TAG_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate LLM output annotation files.")
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR, help="Directory containing LLM result JSON/JSONL files.")
    parser.add_argument("--report", type=Path, default=REPORT_PATH, help="Validation report path.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def load_records(path: Path) -> list[tuple[int, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                rows.append((line_no, json.loads(stripped)))
        return rows

    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        return [(idx, item) for idx, item in enumerate(payload, start=1)]
    return [(1, payload)]


def validate_string_list(value: Any) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    if not isinstance(value, list):
        return [], ["must be a list"]
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            errors.append(f"invalid string item: {item}")
            continue
        stripped = item.strip()
        if not stripped:
            errors.append("empty string item")
            continue
        normalized.append(stripped)
    if len(normalized) != len(set(normalized)):
        errors.append("duplicate values are not allowed")
    return normalized, errors


def validate_record(record: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record is not a JSON object"]

    for field in REQUIRED_FIELDS:
        if field not in record:
            errors.append(f"missing field: {field}")

    if errors:
        return errors

    raw_id = record["raw_id"]
    if not isinstance(raw_id, str) or not raw_id.strip():
        errors.append("raw_id must be a non-empty string")

    keep_status = record["keep_status"]
    if keep_status not in VALID_KEEP_STATUS:
        errors.append(f"invalid keep_status: {keep_status}")

    drop_reason = record["drop_reason"]
    if not isinstance(drop_reason, str):
        errors.append("drop_reason must be a string")
    else:
        drop_reason = drop_reason.strip()
        if keep_status == "drop":
            if drop_reason not in {"non_ai", "vague"}:
                errors.append(f"invalid drop_reason for drop: {drop_reason}")
        elif drop_reason != "":
            errors.append("drop_reason must be empty when keep_status is not drop")

    normalized_question = record["normalized_question"]
    if not isinstance(normalized_question, str):
        errors.append("normalized_question must be a string")

    category_values, category_errors = validate_string_list(record["category"])
    errors.extend([f"category: {msg}" for msg in category_errors])
    for value in category_values:
        if value not in VALID_CATEGORIES:
            errors.append(f"invalid category: {value}")

    tags = record["tags"]
    if not isinstance(tags, list):
        errors.append("tags must be a list")
    else:
        if not 1 <= len(tags) <= 4:
            errors.append("tags must contain between 1 and 4 items")
        for value in tags:
            if not isinstance(value, str) or not TAG_PATTERN.fullmatch(value.strip()):
                errors.append(f"invalid tag: {value}")

    difficulty = record["difficulty"]
    if difficulty not in VALID_DIFFICULTY:
        errors.append(f"invalid difficulty: {difficulty}")

    job_level_values, job_level_errors = validate_string_list(record["job_level"])
    errors.extend([f"job_level: {msg}" for msg in job_level_errors])
    for value in job_level_values:
        if value not in VALID_JOB_LEVEL:
            errors.append(f"invalid job_level: {value}")

    confidence = record["confidence"]
    if not isinstance(confidence, (int, float)):
        errors.append("confidence must be a number")
    else:
        confidence_value = float(confidence)
        if not 0.0 <= confidence_value <= 1.0:
            errors.append(f"confidence must be between 0 and 1: {confidence_value}")

    return errors


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    input_dir = args.input_dir.resolve()
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    input_dir.mkdir(parents=True, exist_ok=True)

    files = sorted([p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".jsonl"}])
    summary_lines: list[str] = []
    total_records = 0
    total_errors = 0

    if not files:
        summary_lines.append(f"No JSON/JSONL files found under {input_dir}")

    for file_path in files:
        file_error_count = 0
        summary_lines.append(f"FILE: {file_path.name}")
        try:
            rows = load_records(file_path)
        except Exception as exc:
            summary_lines.append(f"  FAILED_TO_PARSE: {exc}")
            total_errors += 1
            continue

        summary_lines.append(f"  records: {len(rows)}")
        total_records += len(rows)

        for row_no, record in rows:
            errors = validate_record(record)
            if errors:
                file_error_count += len(errors)
                total_errors += len(errors)
                for error in errors:
                    summary_lines.append(f"  row {row_no}: {error}")

        if file_error_count == 0:
            summary_lines.append("  status: OK")
        else:
            summary_lines.append(f"  status: FAIL ({file_error_count} errors)")
        summary_lines.append("")

    summary_lines.append("SUMMARY")
    summary_lines.append(f"files_checked: {len(files)}")
    summary_lines.append(f"records_checked: {total_records}")
    summary_lines.append(f"errors_found: {total_errors}")
    summary_lines.append(f"result: {'PASS' if total_errors == 0 else 'FAIL'}")

    report_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    logging.info("Validation report written to %s", report_path)
    logging.info("Checked %d files, %d records, %d errors", len(files), total_records, total_errors)
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
