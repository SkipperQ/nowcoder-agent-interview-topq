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
OUTPUT_PATH = ROOT / "data" / "output" / "tag_frequency.xlsx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export category/tag frequency tables for non-drop questions.")
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


def sorted_counter_rows(counter: Counter[Any], headers: list[str]) -> list[list[Any]]:
    rows: list[list[Any]] = [headers]
    for key, count in sorted(counter.items(), key=lambda item: (-item[1], str(item[0]))):
        if isinstance(key, tuple):
            rows.append([*key, count])
        else:
            rows.append([key, count])
    return rows


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input.resolve())
    kept_rows = [row for row in rows if row.get("keep_status") != "drop"]

    category_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    category_tag_counter: Counter[tuple[str, str]] = Counter()

    for row in kept_rows:
        categories = row.get("category") or []
        tags = row.get("tags") or []
        if not isinstance(categories, list) or not isinstance(tags, list):
            raise ValueError(f"Invalid category/tags for raw_id={row.get('raw_id')}")
        for category in categories:
            category_counter[str(category)] += 1
        for tag in tags:
            tag_counter[str(tag)] += 1
        for category in categories:
            for tag in tags:
                category_tag_counter[(str(category), str(tag))] += 1

    sheets = [
        ("category_frequency", sorted_counter_rows(category_counter, ["category", "question_count"])),
        ("tag_frequency", sorted_counter_rows(tag_counter, ["tag", "question_count"])),
        (
            "category_tag_frequency",
            sorted_counter_rows(category_tag_counter, ["category", "tag", "question_count"]),
        ),
    ]
    write_xlsx(args.output.resolve(), sheets)
    print(f"Wrote {len(kept_rows)} non-drop rows to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
