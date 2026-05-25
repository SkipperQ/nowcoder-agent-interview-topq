#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "interim" / "raw_questions.jsonl"
OUTPUT_DIR = ROOT / "data" / "interim" / "llm_batches"
REQUIRED_FIELDS = {
    "raw_id",
    "raw_question",
    "source_post_id",
    "source_index",
    "source_title",
    "company",
    "company_group",
    "specific_company",
    "bu",
    "bu_confidence",
    "department",
    "department_source",
    "department_confidence",
    "post_type",
    "source",
    "source_url",
    "source_file",
    "context",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split raw questions into JSON batch files for LLM annotation.")
    parser.add_argument("--input", type=Path, default=INPUT_PATH, help="Input JSONL path.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Output batch directory.")
    parser.add_argument("--batch-size", type=int, default=40, help="Questions per batch.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def load_jsonl(path: Path) -> list[dict]:
    records = []
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


def validate_records(records: list[dict]) -> None:
    for idx, record in enumerate(records, start=1):
        missing = sorted(REQUIRED_FIELDS - set(record))
        if missing:
            raise ValueError(f"Record {idx} is missing required fields: {', '.join(missing)}")


def chunked(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        logging.error("Input file not found: %s", input_path)
        return 1
    if args.batch_size <= 0:
        logging.error("Batch size must be > 0")
        return 1

    records = load_jsonl(input_path)
    validate_records(records)
    logging.info("Loaded %d questions from %s", len(records), input_path)

    existing = sorted(output_dir.glob("batch_*.json"))
    for path in existing:
        path.unlink()
    if existing:
        logging.info("Removed %d existing batch files from %s", len(existing), output_dir)

    batches = chunked(records, args.batch_size)
    for idx, batch in enumerate(batches, start=1):
        output_path = output_dir / f"batch_{idx:03d}.json"
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False, indent=2)
        logging.info("Wrote %d records to %s", len(batch), output_path.name)

    logging.info("Created %d batch files in %s", len(batches), output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
