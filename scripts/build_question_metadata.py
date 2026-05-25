#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
LABELED_PATH = ROOT / "data" / "interim" / "labeled_raw_questions.jsonl"
MERGE_RESULTS_DIR = ROOT / "data" / "interim" / "merge_results"
OUTPUT_PATH = ROOT / "data" / "interim" / "question_metadata.jsonl"
BUILD_AUDIT_PATH = ROOT / "data" / "interim" / "question_metadata_build_audit.json"

DIFFICULTY_RANK = {"基础": 1, "进阶": 2, "深入": 3, "鍩虹": 1, "杩涢樁": 2, "娣卞叆": 3}
DIFFICULTY_CANONICAL = {1: "基础", 2: "进阶", 3: "深入"}
JOB_LEVEL_ORDER = ["初级", "中级", "高级", "鍒濈骇", "涓骇", "楂樼骇"]
JOB_LEVEL_CANONICAL = {
    "初级": "初级",
    "中级": "中级",
    "高级": "高级",
    "鍒濈骇": "初级",
    "涓骇": "中级",
    "楂樼骇": "高级",
}
UNKNOWN_VALUES = {"", "未知", "鏈煡", None}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canonical question metadata from labeled raw questions and merge results.")
    parser.add_argument("--labeled", type=Path, default=LABELED_PATH, help="labeled_raw_questions.jsonl path.")
    parser.add_argument("--merge-results-dir", type=Path, default=MERGE_RESULTS_DIR, help="Directory containing merge_batch_xxx_result.json files.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Output question_metadata.jsonl path.")
    parser.add_argument("--build-audit", type=Path, default=BUILD_AUDIT_PATH, help="Build audit JSON path.")
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


def load_json_array(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} is not a JSON array")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"{path.name} contains non-object items")
    return payload


def canonicalize_difficulty(value: Any) -> str:
    rank = DIFFICULTY_RANK.get(str(value).strip(), 1)
    return DIFFICULTY_CANONICAL[rank]


def max_difficulty(values: list[Any]) -> str:
    max_rank = max((DIFFICULTY_RANK.get(str(value).strip(), 1) for value in values), default=1)
    return DIFFICULTY_CANONICAL[max_rank]


def canonicalize_job_levels(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for level in JOB_LEVEL_ORDER:
        canonical = JOB_LEVEL_CANONICAL[level]
        if any(JOB_LEVEL_CANONICAL.get(str(v).strip()) == canonical for v in values):
            if canonical not in seen:
                seen.add(canonical)
                result.append(canonical)
    return result


def dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        result.append(stripped)
    return result


def filtered_unknown_dedupe(values: list[str]) -> list[str]:
    cleaned = dedupe_preserve(values)
    non_unknown = [value for value in cleaned if value not in UNKNOWN_VALUES]
    return non_unknown or (cleaned[:1] if cleaned else [])


def infer_source(row: dict[str, Any], audit: dict[str, Any]) -> str:
    source = str(row.get("source", "")).strip()
    if source:
        return source

    source_post_id = str(row.get("source_post_id", "")).strip()
    if source_post_id and "_" in source_post_id:
        inferred = source_post_id.split("_", 1)[0]
        audit["inferred_sources"] += 1
        return inferred

    source_url = str(row.get("source_url", "")).strip()
    if source_url:
        host = urlparse(source_url).netloc.strip().lower()
        if host:
            audit["inferred_sources"] += 1
            return host

    source_file = str(row.get("source_file", "")).strip()
    if source_file:
        audit["inferred_sources"] += 1
        return Path(source_file).stem

    audit["missing_sources"] += 1
    return "unknown"


def aggregate_labeled_fields(rows: list[dict[str, Any]], build_audit: dict[str, Any]) -> dict[str, Any]:
    companies = filtered_unknown_dedupe([str(row.get("company", "")).strip() for row in rows])
    company_groups = filtered_unknown_dedupe([str(row.get("company_group", "")).strip() for row in rows])
    departments = filtered_unknown_dedupe([str(row.get("department", "")).strip() for row in rows])
    sources = filtered_unknown_dedupe([infer_source(row, build_audit) for row in rows])
    source_titles = dedupe_preserve([str(row.get("source_title", "")).strip() for row in rows])[:20]
    source_urls = dedupe_preserve([str(row.get("source_url", "")).strip() for row in rows])[:20]

    frequency = len(rows)
    real_interview_frequency = sum(1 for row in rows if str(row.get("post_type", "")).strip() == "real_interview_post")
    collection_frequency = frequency - real_interview_frequency

    return {
        "frequency": frequency,
        "real_interview_frequency": real_interview_frequency,
        "collection_frequency": collection_frequency,
        "companies": companies,
        "company_groups": company_groups,
        "departments": departments,
        "sources": sources,
        "source_titles": source_titles,
        "source_urls": source_urls,
    }


def build_record(
    qid: str,
    canonical_question: str,
    canonical_item: dict[str, Any],
    labeled_rows: list[dict[str, Any]],
    build_audit: dict[str, Any],
) -> dict[str, Any]:
    aggregates = aggregate_labeled_fields(labeled_rows, build_audit)
    return {
        "id": qid,
        "question": canonical_question,
        "category": dedupe_preserve([str(item).strip() for item in canonical_item.get("category", [])]),
        "difficulty": max_difficulty([canonical_item.get("difficulty")]),
        "frequency": aggregates["frequency"],
        "real_interview_frequency": aggregates["real_interview_frequency"],
        "collection_frequency": aggregates["collection_frequency"],
        "companies": aggregates["companies"],
        "company_groups": aggregates["company_groups"],
        "departments": aggregates["departments"],
        "sources": aggregates["sources"],
        "source_titles": aggregates["source_titles"],
        "source_urls": aggregates["source_urls"],
        "answer": "",
        "related_questions": [],
        "tags": dedupe_preserve([str(item).strip() for item in canonical_item.get("tags", [])]),
        "job_level": canonicalize_job_levels(list(canonical_item.get("job_level", []))),
        "raw_ids": list(canonical_item["raw_ids"]),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    args = parse_args()
    labeled_rows = read_jsonl(args.labeled.resolve())
    labeled_by_raw_id: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(labeled_rows, start=1):
        raw_id = str(row.get("raw_id", "")).strip()
        if not raw_id:
            raise ValueError(f"labeled row {idx} missing raw_id")
        if raw_id in labeled_by_raw_id:
            raise ValueError(f"Duplicate raw_id in labeled_raw_questions.jsonl: {raw_id}")
        labeled_by_raw_id[raw_id] = row

    build_audit: dict[str, Any] = {
        "missing_raw_ids": [],
        "duplicate_raw_ids_across_canonical_items": [],
        "unused_non_drop_raw_ids": [],
        "inferred_sources": 0,
        "missing_sources": 0,
    }

    merge_result_files = sorted(args.merge_results_dir.resolve().glob("merge_batch_*_result.json"))
    records: list[dict[str, Any]] = []
    used_raw_ids: set[str] = set()
    question_counter = 1

    for result_path in merge_result_files:
        merge_rows = load_json_array(result_path)
        for merge_row in merge_rows:
            canonical_items = merge_row.get("canonical_items")
            if not isinstance(canonical_items, list):
                raise ValueError(f"{result_path.name} contains invalid canonical_items")
            for canonical_item in canonical_items:
                raw_ids = canonical_item.get("raw_ids")
                if not isinstance(raw_ids, list) or not raw_ids:
                    raise ValueError(f"{result_path.name} contains canonical item without raw_ids")
                labeled_for_item: list[dict[str, Any]] = []
                for raw_id_value in raw_ids:
                    raw_id = str(raw_id_value).strip()
                    if raw_id in used_raw_ids:
                        build_audit["duplicate_raw_ids_across_canonical_items"].append(raw_id)
                        raise ValueError(f"raw_id appears in multiple canonical items: {raw_id}")
                    labeled_row = labeled_by_raw_id.get(raw_id)
                    if labeled_row is None:
                        build_audit["missing_raw_ids"].append(raw_id)
                        args.build_audit.resolve().write_text(json.dumps(build_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        raise ValueError(f"raw_id not found in labeled_raw_questions.jsonl: {raw_id}")
                    used_raw_ids.add(raw_id)
                    labeled_for_item.append(labeled_row)

                qid = f"Q{question_counter:06d}"
                question_counter += 1
                canonical_question = str(canonical_item.get("canonical_question", "")).strip()
                records.append(build_record(qid, canonical_question, canonical_item, labeled_for_item, build_audit))

    # Add non-drop raw questions that never entered merge candidates as standalone canonical questions.
    standalone_rows = [
        row for row in labeled_rows
        if str(row.get("keep_status", "")).strip() != "drop" and str(row.get("raw_id", "")).strip() not in used_raw_ids
    ]
    build_audit["unused_non_drop_raw_ids"] = [str(row["raw_id"]).strip() for row in standalone_rows]
    for row in standalone_rows:
        raw_id = str(row["raw_id"]).strip()
        canonical_item = {
            "canonical_question": str(row.get("normalized_question", "")).strip() or str(row.get("raw_question", "")).strip(),
            "raw_ids": [raw_id],
            "category": list(row.get("category", [])),
            "tags": list(row.get("tags", [])),
            "difficulty": canonicalize_difficulty(row.get("difficulty")),
            "job_level": list(row.get("job_level", [])),
        }
        qid = f"Q{question_counter:06d}"
        question_counter += 1
        used_raw_ids.add(raw_id)
        records.append(build_record(qid, canonical_item["canonical_question"], canonical_item, [row], build_audit))

    args.build_audit.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.build_audit.resolve().write_text(json.dumps(build_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_jsonl(args.output.resolve(), records)
    print(f"Wrote {len(records)} question metadata rows to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
