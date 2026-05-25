#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from xlsx_utils import write_xlsx


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "interim" / "question_metadata.final.jsonl"
OUTPUT_DIR = ROOT / "data" / "output"

HEADERS = [
    "id",
    "question",
    "category",
    "difficulty",
    "frequency",
    "real_interview_frequency",
    "collection_frequency",
    "companies",
    "company_groups",
    "departments",
    "sources",
    "source_titles",
    "source_urls",
    "answer",
    "related_questions",
    "tags",
    "job_level",
    "raw_ids",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export top-question views from final question metadata JSONL.")
    parser.add_argument("--input", type=Path, default=INPUT_PATH, help="Input question_metadata.final.jsonl path.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Output directory for xlsx files.")
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
        return " | ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def rows_to_sheet(rows: list[dict[str, Any]], extra_headers: list[str] | None = None) -> list[list[Any]]:
    headers = (extra_headers or []) + HEADERS
    table: list[list[Any]] = [headers]
    for row in rows:
        prefix = [row.get(header) for header in (extra_headers or [])]
        values = [format_cell(row.get(header)) for header in HEADERS]
        table.append(prefix + values)
    return table


def sort_rows(rows: list[dict[str, Any]], key_name: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (-int(row.get(key_name, 0)), -int(row.get("frequency", 0)), str(row.get("id", ""))))


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input.resolve())
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    top50 = sort_rows(rows, "frequency")[:50]
    write_xlsx(output_dir / "top50_questions_final.xlsx", [("top50_questions", rows_to_sheet(top50))])

    top50_real = sort_rows(rows, "real_interview_frequency")[:50]
    write_xlsx(
        output_dir / "top50_real_interview_questions_final.xlsx",
        [("top50_real_interview_questions", rows_to_sheet(top50_real))],
    )

    by_category_rows: list[dict[str, Any]] = []
    by_category_real_rows: list[dict[str, Any]] = []
    categories = sorted({category for row in rows for category in row.get("category", [])})
    for category in categories:
        category_rows = [row for row in rows if category in row.get("category", [])]
        for row in sort_rows(category_rows, "frequency")[:10]:
            by_category_rows.append({"category_bucket": category, **row})
        for row in sort_rows(category_rows, "real_interview_frequency")[:10]:
            by_category_real_rows.append({"category_bucket": category, **row})

    write_xlsx(
        output_dir / "top10_by_category_final.xlsx",
        [("top10_by_category", rows_to_sheet(by_category_rows, ["category_bucket"]))],
    )
    write_xlsx(
        output_dir / "top10_real_interview_by_category_final.xlsx",
        [("top10_real_interview_by_category", rows_to_sheet(by_category_real_rows, ["category_bucket"]))],
    )

    print(f"Wrote top-question exports to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
