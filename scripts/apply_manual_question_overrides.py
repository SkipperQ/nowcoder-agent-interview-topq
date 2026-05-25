#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "interim" / "question_metadata.jsonl"
OVERRIDES_PATH = ROOT / "config" / "manual_question_overrides.yml"
OUTPUT_PATH = ROOT / "data" / "interim" / "question_metadata.final.jsonl"

DIFFICULTY_RANK = {"基础": 1, "进阶": 2, "深入": 3}
DIFFICULTY_CANONICAL = {1: "基础", 2: "进阶", 3: "深入"}
JOB_LEVEL_ORDER = ["初级", "中级", "高级"]
JOB_LEVEL_CANONICAL = {"初级": "初级", "中级": "中级", "高级": "高级"}
UNKNOWN_VALUES = {"", "未知", "unknown", None}
MERGEABLE_LIST_FIELDS = [
    "companies",
    "company_groups",
    "departments",
    "sources",
    "source_titles",
    "source_urls",
]
REACT_TAG_KEYWORDS = (
    "react",
    "reason",
    "action",
    "observation",
    "tool",
    "loop",
    "thought",
    "feedback",
    "agent_loop",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply manual drops/merges to question_metadata.jsonl.")
    parser.add_argument("--input", type=Path, default=INPUT_PATH, help="Input question_metadata.jsonl path.")
    parser.add_argument("--overrides", type=Path, default=OVERRIDES_PATH, help="Manual overrides YAML path.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Output final JSONL path.")
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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[0] == '"' and value[-1] == '"':
        return json.loads(value)
    if value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    return value


def split_key_value(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise ValueError(f"Invalid override line: {text}")
    key, value = text.split(":", 1)
    return key.strip(), value.strip()


def load_manual_overrides(path: Path) -> dict[str, list[dict[str, Any]]]:
    payload: dict[str, list[dict[str, Any]]] = {"drops": [], "merges": []}
    section: str | None = None
    current_item: dict[str, Any] | None = None
    current_list_key: str | None = None

    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        if indent == 0:
            section_name = stripped[:-1] if stripped.endswith(":") else ""
            if section_name not in payload:
                raise ValueError(f"{path.name} line {line_no}: unsupported section {section_name!r}")
            section = section_name
            current_item = None
            current_list_key = None
            continue

        if section is None:
            raise ValueError(f"{path.name} line {line_no}: found nested content before section header")

        if indent == 2 and stripped.startswith("- "):
            key, value = split_key_value(stripped[2:])
            current_item = {key: parse_scalar(value)}
            payload[section].append(current_item)
            current_list_key = None
            continue

        if indent == 4 and current_item is not None:
            key, value = split_key_value(stripped)
            if value:
                current_item[key] = parse_scalar(value)
                current_list_key = None
            else:
                current_item[key] = []
                current_list_key = key
            continue

        if indent == 6 and stripped.startswith("- ") and current_item is not None and current_list_key is not None:
            current_item[current_list_key].append(parse_scalar(stripped[2:]))
            continue

        raise ValueError(f"{path.name} line {line_no}: unsupported YAML shape")

    return payload


def dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        stripped = str(value).strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        result.append(stripped)
    return result


def filtered_unknown_dedupe(values: list[str]) -> list[str]:
    cleaned = dedupe_preserve(values)
    non_unknown = [value for value in cleaned if value not in UNKNOWN_VALUES]
    return non_unknown or (cleaned[:1] if cleaned else [])


def max_difficulty(values: list[Any]) -> str:
    max_rank = max((DIFFICULTY_RANK.get(str(value).strip(), 1) for value in values), default=1)
    return DIFFICULTY_CANONICAL[max_rank]


def canonicalize_job_levels(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    normalized = [JOB_LEVEL_CANONICAL.get(str(value).strip(), str(value).strip()) for value in values]
    for level in JOB_LEVEL_ORDER:
        if level in normalized and level not in seen:
            seen.add(level)
            result.append(level)
    return result


def collect_field(rows: list[dict[str, Any]], field_name: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        values.extend(str(item).strip() for item in row.get(field_name, []))
    return values


def merge_tags(target_row: dict[str, Any], source_rows: list[dict[str, Any]], canonical_question: str) -> list[str]:
    merged = dedupe_preserve([str(tag).strip() for tag in target_row.get("tags", [])])
    context_parts = [canonical_question, str(target_row.get("question", ""))]
    context_parts.extend(str(row.get("question", "")) for row in source_rows)
    context_text = " ".join(context_parts).lower()
    react_context = "react" in context_text

    for tag in collect_field(source_rows, "tags"):
        normalized = tag.lower()
        if tag in merged:
            continue
        if react_context and any(keyword in normalized for keyword in REACT_TAG_KEYWORDS):
            merged.append(tag)
    return merged


def merge_record(
    target_row: dict[str, Any],
    source_rows: list[dict[str, Any]],
    merge_spec: dict[str, Any],
) -> dict[str, Any]:
    merged_row = copy.deepcopy(target_row)
    rows = [target_row] + source_rows

    merged_row["question"] = str(merge_spec.get("canonical_question", "")).strip() or str(target_row.get("question", "")).strip()
    merged_row["raw_ids"] = dedupe_preserve(collect_field(rows, "raw_ids"))
    merged_row["frequency"] = len(merged_row["raw_ids"])
    merged_row["real_interview_frequency"] = sum(int(row.get("real_interview_frequency", 0)) for row in rows)
    merged_row["collection_frequency"] = sum(int(row.get("collection_frequency", 0)) for row in rows)

    for field_name in MERGEABLE_LIST_FIELDS:
        merged_row[field_name] = filtered_unknown_dedupe(collect_field(rows, field_name))

    merged_row["tags"] = merge_tags(target_row, source_rows, merged_row["question"])
    merged_row["category"] = dedupe_preserve(collect_field(rows, "category"))
    merged_row["difficulty"] = max_difficulty([row.get("difficulty") for row in rows])
    merged_row["job_level"] = canonicalize_job_levels(collect_field(rows, "job_level"))
    merged_row["related_questions"] = list(target_row.get("related_questions", []))
    merged_row["answer"] = target_row.get("answer", "")
    return merged_row


def validate_overrides(rows: list[dict[str, Any]], overrides: dict[str, list[dict[str, Any]]]) -> None:
    ids = [str(row.get("id", "")).strip() for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("question_metadata.jsonl contains duplicate question ids")

    known_ids = set(ids)
    drop_ids = [str(item.get("id", "")).strip() for item in overrides.get("drops", [])]
    for drop_id in drop_ids:
        if drop_id not in known_ids:
            raise ValueError(f"Drop id not found in question metadata: {drop_id}")

    seen_sources: set[str] = set()
    for merge in overrides.get("merges", []):
        target_id = str(merge.get("target_id", "")).strip()
        source_ids = [str(item).strip() for item in merge.get("source_ids", [])]
        if target_id not in known_ids:
            raise ValueError(f"Merge target_id not found in question metadata: {target_id}")
        if not source_ids:
            raise ValueError(f"Merge for {target_id} has no source_ids")
        if target_id in drop_ids:
            raise ValueError(f"Merge target_id cannot also be dropped: {target_id}")
        for source_id in source_ids:
            if source_id not in known_ids:
                raise ValueError(f"Merge source_id not found in question metadata: {source_id}")
            if source_id == target_id:
                raise ValueError(f"Merge source_id cannot equal target_id: {target_id}")
            if source_id in drop_ids:
                raise ValueError(f"Merge source_id cannot also be dropped: {source_id}")
            if source_id in seen_sources:
                raise ValueError(f"Merge source_id appears in multiple merges: {source_id}")
            seen_sources.add(source_id)


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input.resolve())
    overrides = load_manual_overrides(args.overrides.resolve())
    validate_overrides(rows, overrides)

    rows_by_id = {str(row["id"]).strip(): row for row in rows}
    drop_ids = {str(item["id"]).strip() for item in overrides.get("drops", [])}
    source_to_target: dict[str, str] = {}
    merged_by_target: dict[str, dict[str, Any]] = {}

    for merge in overrides.get("merges", []):
        target_id = str(merge["target_id"]).strip()
        source_ids = [str(item).strip() for item in merge.get("source_ids", [])]
        source_rows = [rows_by_id[source_id] for source_id in source_ids]
        merged_by_target[target_id] = merge_record(rows_by_id[target_id], source_rows, merge)
        for source_id in source_ids:
            source_to_target[source_id] = target_id

    final_rows: list[dict[str, Any]] = []
    for row in rows:
        row_id = str(row["id"]).strip()
        if row_id in drop_ids or row_id in source_to_target:
            continue
        if row_id in merged_by_target:
            final_rows.append(merged_by_target[row_id])
        else:
            final_rows.append(copy.deepcopy(row))

    write_jsonl(args.output.resolve(), final_rows)
    print(
        f"Wrote {len(final_rows)} rows to {args.output.resolve()} "
        f"(drops={len(drop_ids)}, merges={len(merged_by_target)}, merged_sources={len(source_to_target)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
