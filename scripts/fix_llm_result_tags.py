#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT / "data" / "interim" / "llm_results"
DEFAULT_REPORT = ROOT / "data" / "interim" / "tag_fix_report.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fix tag-count validation issues in LLM result files.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR, help="Directory containing result JSON files.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Where to write the fix report.")
    parser.add_argument("--files", nargs="*", help="Optional explicit file names to process.")
    return parser.parse_args()


def load_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} is not a JSON array")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"{path.name} contains non-object items")
    return payload


def choose_fallback_tag(record: dict[str, Any]) -> str:
    drop_reason = str(record.get("drop_reason", "")).strip()
    if drop_reason in {"non_ai", "vague"}:
        return drop_reason
    keep_status = str(record.get("keep_status", "")).strip()
    if keep_status == "drop":
        return "dropped"
    return "uncategorized"


def normalize_tags(record: dict[str, Any]) -> tuple[list[str], list[str]]:
    tags = record.get("tags")
    if not isinstance(tags, list):
        tags = []

    cleaned: list[str] = []
    changes: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        stripped = tag.strip()
        if stripped and stripped not in cleaned:
            cleaned.append(stripped)

    if len(cleaned) != len(tags):
        changes.append("deduped_or_removed_invalid")

    if not cleaned:
        cleaned = [choose_fallback_tag(record)]
        changes.append("filled_empty")

    if len(cleaned) > 4:
        cleaned = cleaned[:4]
        changes.append("trimmed_to_4")

    return cleaned, changes


def iter_target_files(input_dir: Path, explicit_files: list[str] | None) -> list[Path]:
    if explicit_files:
        return [input_dir / name for name in explicit_files]
    return sorted(input_dir.glob("batch_*_result.json"))


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report_lines: list[str] = []
    touched_files = 0
    touched_records = 0

    for path in iter_target_files(input_dir, args.files):
        data = load_json(path)
        file_changes: list[str] = []
        changed = False

        for idx, record in enumerate(data, start=1):
            new_tags, changes = normalize_tags(record)
            if changes and record.get("tags") != new_tags:
                record["tags"] = new_tags
                changed = True
                touched_records += 1
                file_changes.append(f"  row {idx}: {', '.join(changes)} -> {new_tags}")

        if changed:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            touched_files += 1
            report_lines.append(f"FILE: {path.name}")
            report_lines.extend(file_changes)
            report_lines.append("")

    report_lines.append(f"files_touched: {touched_files}")
    report_lines.append(f"records_touched: {touched_records}")
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"Wrote fix report to {report_path}")
    print(f"files_touched={touched_files}")
    print(f"records_touched={touched_records}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
