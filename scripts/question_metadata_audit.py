#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from xlsx_utils import write_xlsx


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "interim" / "question_metadata.jsonl"
BUILD_AUDIT_PATH = ROOT / "data" / "interim" / "question_metadata_build_audit.json"
OUTPUT_PATH = ROOT / "data" / "output" / "question_metadata_audit.xlsx"

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
    parser = argparse.ArgumentParser(description="Build question metadata audit workbook.")
    parser.add_argument("--input", type=Path, default=INPUT_PATH, help="Input question_metadata.jsonl path.")
    parser.add_argument("--build-audit", type=Path, default=BUILD_AUDIT_PATH, help="Build audit JSON path.")
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
        return " | ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def rows_to_sheet(rows: list[dict[str, Any]], extra_headers: list[str] | None = None) -> list[list[Any]]:
    headers = (extra_headers or []) + HEADERS
    table: list[list[Any]] = [headers]
    for row in rows:
        prefix = [format_cell(row.get(header)) for header in (extra_headers or [])]
        values = [format_cell(row.get(header)) for header in HEADERS]
        table.append(prefix + values)
    return table


def sort_rows(rows: list[dict[str, Any]], key_name: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (-int(row.get(key_name, 0)), -int(row.get("frequency", 0)), str(row.get("id", ""))))


def is_suspicious(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    question = str(row.get("question", "")).strip()
    tags = list(row.get("tags", []))
    categories = list(row.get("category", []))
    companies = list(row.get("companies", []))
    raw_ids = list(row.get("raw_ids", []))
    frequency = int(row.get("frequency", 0))
    real_frequency = int(row.get("real_interview_frequency", 0))

    if len(question) < 8:
        reasons.append("question_too_short")
    broad_phrases = ["系统怎么设计", "如何优化", "怎么设计", "如何设计"]
    if any(phrase in question for phrase in broad_phrases) and len(tags) >= 4:
        reasons.append("question_too_broad_with_many_tags")
    if frequency >= 5 and real_frequency <= 1:
        reasons.append("high_frequency_but_low_real_interview_frequency")
    if len(tags) > 8:
        reasons.append("too_many_tags")
    if len(categories) > 3:
        reasons.append("too_many_categories")
    if not companies:
        reasons.append("companies_empty")
    if not raw_ids:
        reasons.append("raw_ids_empty")
    return reasons


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input.resolve())
    build_audit = {}
    if args.build_audit.resolve().exists():
        build_audit = json.loads(args.build_audit.resolve().read_text(encoding="utf-8"))

    total_raw_id_count = sum(len(row.get("raw_ids", [])) for row in rows)
    avg_frequency = round(total_raw_id_count / len(rows), 4) if rows else 0.0
    max_frequency = max((int(row.get("frequency", 0)) for row in rows), default=0)
    real_interview_question_count = sum(1 for row in rows if int(row.get("real_interview_frequency", 0)) > 0)
    collection_only_question_count = sum(1 for row in rows if int(row.get("real_interview_frequency", 0)) == 0)

    category_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    for row in rows:
        for category in row.get("category", []):
            category_counter[str(category)] += 1
        for tag in row.get("tags", []):
            tag_counter[str(tag)] += 1

    suspicious_rows: list[dict[str, Any]] = []
    for row in rows:
        reasons = is_suspicious(row)
        if reasons:
            suspicious_rows.append({"reasons": " | ".join(reasons), **row})

    build_issue_rows: list[list[Any]] = [["issue_type", "value"]]
    for key, value in build_audit.items():
        if isinstance(value, list):
            if value:
                for item in value:
                    build_issue_rows.append([key, item])
        else:
            build_issue_rows.append([key, value])
    if len(build_issue_rows) == 1:
        build_issue_rows.append(["build_audit", "no_issues"])

    sheets = [
        (
            "summary",
            [
                ["metric", "value"],
                ["canonical_question_count", len(rows)],
                ["total_raw_id_count", total_raw_id_count],
                ["avg_frequency", avg_frequency],
                ["max_frequency", max_frequency],
                ["real_interview_question_count", real_interview_question_count],
                ["collection_only_question_count", collection_only_question_count],
            ],
        ),
        ("top_frequency", rows_to_sheet(sort_rows(rows, "frequency")[:100])),
        ("top_real_interview_frequency", rows_to_sheet(sort_rows(rows, "real_interview_frequency")[:100])),
        (
            "category_distribution",
            [["category", "question_count"]]
            + [[category, count] for category, count in sorted(category_counter.items(), key=lambda item: (-item[1], item[0]))],
        ),
        (
            "tag_distribution",
            [["tag", "question_count"]]
            + [[tag, count] for tag, count in sorted(tag_counter.items(), key=lambda item: (-item[1], item[0]))],
        ),
        ("suspicious_questions", rows_to_sheet(suspicious_rows, ["reasons"])),
        ("build_issues", build_issue_rows),
    ]

    write_xlsx(args.output.resolve(), sheets)
    print(f"Wrote question metadata audit workbook to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
