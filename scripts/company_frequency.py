#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from xlsx_utils import write_xlsx


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "interim" / "labeled_raw_questions.jsonl"
OUTPUT_PATH = ROOT / "data" / "output" / "company_frequency.xlsx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export company/department/post_type frequency tables.")
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


def normalize_key(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return text or "UNKNOWN"


def counter_sheet(counter: Counter[str], headers: list[str]) -> list[list[Any]]:
    rows: list[list[Any]] = [headers]
    for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        rows.append([key, count])
    return rows


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input.resolve())

    company_counter: Counter[str] = Counter()
    department_counter: Counter[str] = Counter()
    post_type_counter: Counter[str] = Counter()

    for row in rows:
        company_counter[normalize_key(row.get("company"))] += 1
        department_counter[normalize_key(row.get("department"))] += 1
        post_type_counter[normalize_key(row.get("post_type"))] += 1

    sheets = [
        ("company_frequency", counter_sheet(company_counter, ["company", "question_count"])),
        ("department_frequency", counter_sheet(department_counter, ["department", "question_count"])),
        ("post_type_frequency", counter_sheet(post_type_counter, ["post_type", "question_count"])),
    ]
    write_xlsx(args.output.resolve(), sheets)
    print(f"Wrote frequency tables for {len(rows)} rows to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
