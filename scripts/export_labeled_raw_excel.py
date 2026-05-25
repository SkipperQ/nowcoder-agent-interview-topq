#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from xlsx_utils import write_xlsx


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "interim" / "labeled_raw_questions.jsonl"
OUTPUT_PATH = ROOT / "data" / "output" / "raw_questions_labeled.xlsx"

HEADERS = [
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
    parser = argparse.ArgumentParser(description="Export labeled raw questions JSONL to XLSX.")
    parser.add_argument("--input", type=Path, default=INPUT_PATH, help="Input labeled JSONL path.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Output XLSX path.")
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


def format_cell(value: Any) -> Any:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input.resolve())
    sheet_rows: list[list[Any]] = [HEADERS]
    for row in rows:
        sheet_rows.append([format_cell(row.get(header)) for header in HEADERS])
    write_xlsx(args.output.resolve(), [("raw_questions_labeled", sheet_rows)])
    print(f"Wrote {len(rows)} rows to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
