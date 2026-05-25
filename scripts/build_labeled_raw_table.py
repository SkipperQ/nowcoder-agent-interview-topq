#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW_QUESTIONS_PATH = ROOT / "data" / "interim" / "raw_questions.jsonl"
LLM_RESULTS_DIR = ROOT / "data" / "interim" / "llm_results"
OUTPUT_PATH = ROOT / "data" / "interim" / "labeled_raw_questions.jsonl"

SOURCE_FIELDS = [
    "raw_id",
    "raw_question",
    "source_post_id",
    "source_index",
    "source_title",
    "company",
    "company_group",
    "specific_company",
    "bu",
    "department",
    "post_type",
    "source_url",
    "source_file",
    "context",
]

LABEL_FIELDS = [
    "keep_status",
    "drop_reason",
    "normalized_question",
    "category",
    "tags",
    "difficulty",
    "job_level",
    "confidence",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build labeled raw question table by joining raw_questions and llm_results.")
    parser.add_argument("--raw-questions", type=Path, default=RAW_QUESTIONS_PATH, help="Input raw_questions JSONL path.")
    parser.add_argument("--llm-results-dir", type=Path, default=LLM_RESULTS_DIR, help="Directory containing batch_xxx_result.json files.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Output labeled JSONL path.")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                raise ValueError(f"{path.name} line {line_no} is not a JSON object")
            rows.append(payload)
    return rows


def read_llm_results(results_dir: Path) -> dict[str, dict[str, Any]]:
    by_raw_id: dict[str, dict[str, Any]] = {}
    for path in sorted(results_dir.glob("batch_*_result.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"{path.name} is not a JSON array")
        for idx, row in enumerate(payload, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"{path.name} row {idx} is not a JSON object")
            raw_id = row.get("raw_id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise ValueError(f"{path.name} row {idx} missing valid raw_id")
            normalized_raw_id = raw_id.strip()
            if normalized_raw_id in by_raw_id:
                raise ValueError(f"Duplicate raw_id in llm_results: {normalized_raw_id}")
            by_raw_id[normalized_raw_id] = row
    return by_raw_id


def select_fields(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: record.get(field) for field in fields}


def main() -> int:
    args = parse_args()
    raw_questions_path = args.raw_questions.resolve()
    llm_results_dir = args.llm_results_dir.resolve()
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw_rows = read_jsonl(raw_questions_path)
    llm_by_raw_id = read_llm_results(llm_results_dir)

    seen_raw_ids: set[str] = set()
    output_rows: list[dict[str, Any]] = []

    for idx, raw_row in enumerate(raw_rows, start=1):
        raw_id = raw_row.get("raw_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError(f"raw_questions row {idx} missing valid raw_id")
        normalized_raw_id = raw_id.strip()
        if normalized_raw_id in seen_raw_ids:
            raise ValueError(f"Duplicate raw_id in raw_questions: {normalized_raw_id}")
        seen_raw_ids.add(normalized_raw_id)

        label_row = llm_by_raw_id.get(normalized_raw_id)
        if label_row is None:
            raise ValueError(f"Missing llm label for raw_id: {normalized_raw_id}")

        combined = {}
        combined.update(select_fields(raw_row, SOURCE_FIELDS))
        combined.update(select_fields(label_row, LABEL_FIELDS))
        output_rows.append(combined)

    unused_label_ids = sorted(set(llm_by_raw_id) - seen_raw_ids)
    if unused_label_ids:
        raise ValueError(f"Found {len(unused_label_ids)} llm_results raw_id values not present in raw_questions")

    with output_path.open("w", encoding="utf-8") as f:
        for row in output_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Wrote {len(output_rows)} rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
