#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_PATH = ROOT / "data" / "interim" / "merge_candidates.json"
RESULTS_DIR = ROOT / "data" / "interim" / "merge_results"
REPORT_PATH = ROOT / "data" / "interim" / "merge_validation_report.txt"

VALID_CATEGORIES = {"Agent", "RAG", "AICoding", "Prompt", "MCP", "CLI", "Evaluation", "LLM", "OpenEnded"}
VALID_DIFFICULTY = {"基础", "进阶", "深入"}
VALID_JOB_LEVEL = {"初级", "中级", "高级"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate merge_batch_xxx_result.json files.")
    parser.add_argument("--candidates", type=Path, default=CANDIDATES_PATH, help="merge_candidates.json path.")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help="Directory containing merge results.")
    parser.add_argument("--report", type=Path, default=REPORT_PATH, help="Validation report path.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def load_json_array(path: Path) -> list[Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} is not a JSON array")
    return payload


def validate_string_list(value: Any, field_name: str) -> tuple[list[str], list[str]]:
    if not isinstance(value, list):
        return [], [f"{field_name} must be a list"]
    normalized: list[str] = []
    errors: list[str] = []
    for item in value:
        if not isinstance(item, str):
            errors.append(f"{field_name} contains non-string item: {item}")
            continue
        stripped = item.strip()
        if not stripped:
            errors.append(f"{field_name} contains empty string")
            continue
        normalized.append(stripped)
    return normalized, errors


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    candidate_rows = load_json_array(args.candidates.resolve())
    candidates_by_id: dict[str, set[str]] = {}
    for row in candidate_rows:
        if not isinstance(row, dict):
            raise ValueError("merge_candidates.json contains non-object items")
        candidate_group_id = str(row.get("candidate_group_id", "")).strip()
        questions = row.get("questions")
        if not candidate_group_id or not isinstance(questions, list):
            raise ValueError("Invalid candidate group entry in merge_candidates.json")
        raw_ids: set[str] = set()
        for question in questions:
            if not isinstance(question, dict):
                raise ValueError(f"{candidate_group_id} contains non-object question")
            raw_id = str(question.get("raw_id", "")).strip()
            if not raw_id:
                raise ValueError(f"{candidate_group_id} contains invalid raw_id")
            raw_ids.add(raw_id)
        candidates_by_id[candidate_group_id] = raw_ids

    report_lines: list[str] = []
    total_errors = 0
    results_dir = args.results_dir.resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    result_files = sorted(results_dir.glob("merge_batch_*_result.json"))

    if not result_files:
        report_lines.append(f"No merge result files found under {results_dir}")

    for path in result_files:
        file_errors = 0
        report_lines.append(f"FILE: {path.name}")
        try:
            payload = load_json_array(path)
        except Exception as exc:
            report_lines.append(f"  FAILED_TO_PARSE: {exc}")
            total_errors += 1
            continue

        for row_idx, row in enumerate(payload, start=1):
            prefix = f"  row {row_idx}:"
            if not isinstance(row, dict):
                report_lines.append(f"{prefix} item is not a JSON object")
                file_errors += 1
                total_errors += 1
                continue

            candidate_group_id = str(row.get("candidate_group_id", "")).strip()
            if not candidate_group_id:
                report_lines.append(f"{prefix} candidate_group_id is required")
                file_errors += 1
                total_errors += 1
                continue
            if candidate_group_id not in candidates_by_id:
                report_lines.append(f"{prefix} unknown candidate_group_id: {candidate_group_id}")
                file_errors += 1
                total_errors += 1
                continue

            canonical_items = row.get("canonical_items")
            if not isinstance(canonical_items, list):
                report_lines.append(f"{prefix} canonical_items must be a list")
                file_errors += 1
                total_errors += 1
                continue

            source_raw_ids = candidates_by_id[candidate_group_id]
            seen_raw_ids: set[str] = set()

            for item_idx, item in enumerate(canonical_items, start=1):
                item_prefix = f"{prefix} canonical_items[{item_idx}]"
                if not isinstance(item, dict):
                    report_lines.append(f"{item_prefix} is not a JSON object")
                    file_errors += 1
                    total_errors += 1
                    continue

                canonical_question = str(item.get("canonical_question", "")).strip()
                if not canonical_question:
                    report_lines.append(f"{item_prefix} canonical_question must be non-empty")
                    file_errors += 1
                    total_errors += 1

                raw_ids, raw_id_errors = validate_string_list(item.get("raw_ids"), "raw_ids")
                for error in raw_id_errors:
                    report_lines.append(f"{item_prefix} {error}")
                    file_errors += 1
                    total_errors += 1
                for raw_id in raw_ids:
                    if raw_id not in source_raw_ids:
                        report_lines.append(f"{item_prefix} raw_id not in candidate group: {raw_id}")
                        file_errors += 1
                        total_errors += 1
                    if raw_id in seen_raw_ids:
                        report_lines.append(f"{item_prefix} raw_id appears in multiple canonical_items: {raw_id}")
                        file_errors += 1
                        total_errors += 1
                    seen_raw_ids.add(raw_id)

                categories, category_errors = validate_string_list(item.get("category"), "category")
                for error in category_errors:
                    report_lines.append(f"{item_prefix} {error}")
                    file_errors += 1
                    total_errors += 1
                for category in categories:
                    if category not in VALID_CATEGORIES:
                        report_lines.append(f"{item_prefix} invalid category: {category}")
                        file_errors += 1
                        total_errors += 1

                difficulty = str(item.get("difficulty", "")).strip()
                if difficulty not in VALID_DIFFICULTY:
                    report_lines.append(f"{item_prefix} invalid difficulty: {difficulty}")
                    file_errors += 1
                    total_errors += 1

                job_levels, job_level_errors = validate_string_list(item.get("job_level"), "job_level")
                for error in job_level_errors:
                    report_lines.append(f"{item_prefix} {error}")
                    file_errors += 1
                    total_errors += 1
                for job_level in job_levels:
                    if job_level not in VALID_JOB_LEVEL:
                        report_lines.append(f"{item_prefix} invalid job_level: {job_level}")
                        file_errors += 1
                        total_errors += 1

        report_lines.append(f"  status: {'OK' if file_errors == 0 else f'FAIL ({file_errors} errors)'}")
        report_lines.append("")

    report_lines.append("SUMMARY")
    report_lines.append(f"files_checked: {len(result_files)}")
    report_lines.append(f"errors_found: {total_errors}")
    report_lines.append(f"result: {'PASS' if total_errors == 0 else 'FAIL'}")

    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    logging.info("Validation report written to %s", report_path)
    logging.info("Checked %d files, %d errors", len(result_files), total_errors)
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
