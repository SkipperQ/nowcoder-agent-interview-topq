#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "interim" / "merge_candidates.json"
OUTPUT_DIR = ROOT / "data" / "interim" / "merge_batches"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split merge candidates into review batches.")
    parser.add_argument("--input", type=Path, default=INPUT_PATH, help="merge_candidates.json path.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Directory for merge_batch_xxx.json files.")
    parser.add_argument("--batch-size", type=int, default=20, help="Candidate groups per review batch.")
    return parser.parse_args()


def load_candidates(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} is not a JSON array")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"{path.name} contains non-object items")
    return payload


def main() -> int:
    args = parse_args()
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be > 0")

    candidates = load_candidates(args.input.resolve())
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(output_dir.glob("merge_batch_*.json"))
    for path in existing:
        path.unlink()

    batch_count = 0
    for idx in range(0, len(candidates), args.batch_size):
        chunk = candidates[idx:idx + args.batch_size]
        batch_count += 1
        output_path = output_dir / f"merge_batch_{batch_count:03d}.json"
        output_path.write_text(json.dumps(chunk, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {batch_count} merge review batches to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
