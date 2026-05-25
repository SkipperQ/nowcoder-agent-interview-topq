#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from xlsx_utils import write_xlsx


ROOT = Path(__file__).resolve().parents[1]
SOURCE_POSTS_PATH = ROOT / "data" / "interim" / "source_posts.jsonl"
RAW_QUESTIONS_PATH = ROOT / "data" / "interim" / "raw_questions.jsonl"
OUTPUT_PATH = ROOT / "data" / "output" / "source_post_audit.xlsx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit source posts and extracted questions.")
    parser.add_argument("--source-posts", type=Path, default=SOURCE_POSTS_PATH, help="Input source_posts JSONL path.")
    parser.add_argument("--raw-questions", type=Path, default=RAW_QUESTIONS_PATH, help="Input raw_questions JSONL path.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Output Excel path.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no} of {path}: {exc}") from exc
    return rows


def build_question_count_map(raw_questions: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in raw_questions:
        source_post_id = row.get("source_post_id", "")
        counts[source_post_id] = counts.get(source_post_id, 0) + 1
    return counts


def build_summary_rows(source_posts: list[dict], raw_questions: list[dict]) -> list[list]:
    unique_companies = {row.get("company", "未知") for row in source_posts}
    unknown_company_count = sum(1 for row in source_posts if row.get("company", "未知") == "未知")
    known_department_count = sum(1 for row in source_posts if row.get("department", "未知") != "未知")
    unknown_department_count = len(source_posts) - known_department_count
    return [
        ["metric", "value"],
        ["source_post_count", len(source_posts)],
        ["question_count", len(raw_questions)],
        ["unique_company_count", len(unique_companies)],
        ["unknown_company_count", unknown_company_count],
        ["known_department_count", known_department_count],
        ["unknown_department_count", unknown_department_count],
    ]


def build_source_posts_rows(source_posts: list[dict], question_counts: dict[str, int]) -> list[list]:
    rows = [[
        "source_post_id", "source_index", "source_title", "company", "company_group",
        "specific_company", "bu", "bu_confidence", "department", "department_confidence",
        "question_count", "source_url", "source_file",
    ]]
    for row in source_posts:
        rows.append([
            row.get("source_post_id", ""),
            row.get("source_index", ""),
            row.get("source_title", ""),
            row.get("company", ""),
            row.get("company_group", ""),
            row.get("specific_company", ""),
            row.get("bu", ""),
            row.get("bu_confidence", ""),
            row.get("department", ""),
            row.get("department_confidence", ""),
            question_counts.get(row.get("source_post_id", ""), 0),
            row.get("source_url", ""),
            row.get("source_file", ""),
        ])
    return rows


def build_company_frequency_rows(source_posts: list[dict], question_counts: dict[str, int]) -> list[list]:
    stats: dict[str, dict[str, int]] = {}
    for row in source_posts:
        company = row.get("company", "未知")
        item = stats.setdefault(company, {"source_post_count": 0, "question_count": 0})
        item["source_post_count"] += 1
        item["question_count"] += question_counts.get(row.get("source_post_id", ""), 0)
    rows = [["company", "source_post_count", "question_count"]]
    for company, item in sorted(stats.items(), key=lambda x: (-x[1]["source_post_count"], x[0])):
        rows.append([company, item["source_post_count"], item["question_count"]])
    return rows


def build_department_frequency_rows(source_posts: list[dict], question_counts: dict[str, int]) -> list[list]:
    stats: dict[tuple[str, str, str], dict[str, int]] = {}
    for row in source_posts:
        key = (row.get("company", "未知"), row.get("bu", "未知"), row.get("department", "未知"))
        item = stats.setdefault(key, {"source_post_count": 0, "question_count": 0})
        item["source_post_count"] += 1
        item["question_count"] += question_counts.get(row.get("source_post_id", ""), 0)
    rows = [["company", "bu", "department", "source_post_count", "question_count"]]
    for (company, bu, department), item in sorted(stats.items(), key=lambda x: (-x[1]["source_post_count"], x[0][0], x[0][1], x[0][2])):
        rows.append([company, bu, department, item["source_post_count"], item["question_count"]])
    return rows


def is_suspicious_post(row: dict, question_count: int) -> bool:
    title = row.get("source_title", "")
    return any([
        row.get("company", "未知") == "未知",
        not title or len(title.strip()) < 4,
        question_count == 0,
        question_count > 50,
        not row.get("source_url", "").strip(),
        str(row.get("department_confidence", "")).lower() == "high" and row.get("department", "未知") == "未知",
    ])


def suspicious_reason(row: dict, question_count: int) -> str:
    reasons: list[str] = []
    title = row.get("source_title", "")
    if row.get("company", "未知") == "未知":
        reasons.append("company=未知")
    if not title or len(title.strip()) < 4:
        reasons.append("source_title为空或过短")
    if question_count == 0:
        reasons.append("question_count=0")
    if question_count > 50:
        reasons.append("question_count>50")
    if not row.get("source_url", "").strip():
        reasons.append("source_url为空")
    if str(row.get("department_confidence", "")).lower() == "high" and row.get("department", "未知") == "未知":
        reasons.append("department_confidence=high但department=未知")
    return "; ".join(reasons)


def build_suspicious_rows(source_posts: list[dict], question_counts: dict[str, int]) -> list[list]:
    rows = [[
        "source_post_id", "source_index", "source_title", "company", "bu", "department",
        "department_confidence", "question_count", "source_url", "source_file", "reason",
    ]]
    for row in source_posts:
        question_count = question_counts.get(row.get("source_post_id", ""), 0)
        if is_suspicious_post(row, question_count):
            rows.append([
                row.get("source_post_id", ""),
                row.get("source_index", ""),
                row.get("source_title", ""),
                row.get("company", ""),
                row.get("bu", ""),
                row.get("department", ""),
                row.get("department_confidence", ""),
                question_count,
                row.get("source_url", ""),
                row.get("source_file", ""),
                suspicious_reason(row, question_count),
            ])
    return rows


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    source_posts_path = args.source_posts.resolve()
    raw_questions_path = args.raw_questions.resolve()
    output_path = args.output.resolve()

    if not source_posts_path.exists():
        logging.error("source_posts file not found: %s", source_posts_path)
        return 1
    if not raw_questions_path.exists():
        logging.error("raw_questions file not found: %s", raw_questions_path)
        return 1

    source_posts = load_jsonl(source_posts_path)
    raw_questions = load_jsonl(raw_questions_path)
    question_counts = build_question_count_map(raw_questions)

    sheets = [
        ("summary", build_summary_rows(source_posts, raw_questions)),
        ("source_posts", build_source_posts_rows(source_posts, question_counts)),
        ("company_frequency", build_company_frequency_rows(source_posts, question_counts)),
        ("department_frequency", build_department_frequency_rows(source_posts, question_counts)),
        ("suspicious_posts", build_suspicious_rows(source_posts, question_counts)),
    ]
    write_xlsx(output_path, sheets)
    logging.info("Wrote source post audit workbook to %s", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
