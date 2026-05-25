#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import random
from pathlib import Path

from xlsx_utils import write_xlsx


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "interim" / "raw_questions.jsonl"
OUTPUT_PATH = ROOT / "data" / "output" / "department_audit.xlsx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit department extraction coverage from raw question metadata.")
    parser.add_argument("--input", type=Path, default=INPUT_PATH, help="Input raw_questions JSONL path.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Output Excel path.")
    parser.add_argument("--sample-size", type=int, default=200, help="Number of unknown-department samples to export.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for unknown sample selection.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no} of {path}: {exc}") from exc
    return records


def normalize_confidence(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    mapping = {"high": 0.9, "medium": 0.6, "low": 0.3, "未知": 0.0, "unknown": 0.0}
    return mapping.get(text, 0.0)


def build_summary_rows(records: list[dict]) -> list[list]:
    total = len(records)
    unknown = sum(1 for row in records if row.get("department", "未知") == "未知")
    known = total - unknown
    ratio = round((known / total) if total else 0.0, 4)
    return [
        ["metric", "value"],
        ["总问题数", total],
        ["department=未知 的数量", unknown],
        ["department 非未知的数量", known],
        ["department 识别率", ratio],
    ]


def build_frequency_rows(records: list[dict]) -> list[list]:
    stats: dict[tuple[str, str], dict[str, float]] = {}
    for row in records:
        key = (row.get("company", "未知"), row.get("department", "未知"))
        item = stats.setdefault(key, {"count": 0, "confidence_sum": 0.0})
        item["count"] += 1
        item["confidence_sum"] += normalize_confidence(row.get("department_confidence", 0.0))

    rows = [["company", "department", "count", "avg_confidence"]]
    for (company, department), item in sorted(stats.items(), key=lambda x: (-x[1]["count"], x[0][0], x[0][1])):
        avg = round(item["confidence_sum"] / item["count"], 4) if item["count"] else 0.0
        rows.append([company, department, item["count"], avg])
    return rows


def build_unknown_sample_rows(records: list[dict], sample_size: int, seed: int) -> list[list]:
    unknown_rows = [row for row in records if row.get("department", "未知") == "未知"]
    if len(unknown_rows) > sample_size:
        unknown_rows = random.Random(seed).sample(unknown_rows, sample_size)
    unknown_rows = sorted(unknown_rows, key=lambda row: row.get("raw_id", ""))

    rows = [["raw_id", "company", "source_title", "source_file", "raw_question", "context"]]
    for row in unknown_rows:
        rows.append([
            row.get("raw_id", ""),
            row.get("company", ""),
            row.get("source_title", ""),
            row.get("source_file", ""),
            row.get("raw_question", ""),
            row.get("context", ""),
        ])
    return rows

def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    input_path = args.input.resolve()
    output_path = args.output.resolve()

    if not input_path.exists():
        logging.error("Input file not found: %s", input_path)
        return 1
    if args.sample_size <= 0:
        logging.error("Sample size must be > 0")
        return 1

    records = load_jsonl(input_path)
    logging.info("Loaded %d records from %s", len(records), input_path)

    sheets = [
        ("summary", build_summary_rows(records)),
        ("department_frequency", build_frequency_rows(records)),
        ("unknown_department_samples", build_unknown_sample_rows(records, args.sample_size, args.seed)),
    ]
    write_xlsx(output_path, sheets)
    logging.info("Wrote department audit workbook to %s", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
