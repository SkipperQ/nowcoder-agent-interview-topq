#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "interim" / "labeled_raw_questions.jsonl"
OUTPUT_PATH = ROOT / "data" / "interim" / "merge_candidates.json"

ASCII_TOKEN_RE = re.compile(r"[a-z0-9_#+.-]{2,}", re.IGNORECASE)
CJK_SPAN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


@dataclass
class QuestionRecord:
    raw_id: str
    raw_question: str
    normalized_question: str
    category: list[str]
    tags: list[str]
    difficulty: str
    job_level: list[str]
    confidence: float
    company: str
    department: str
    post_type: str
    source_title: str
    tokens: set[str]
    keywords: set[str]


class DisjointSet:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}
        self.rank = {value: 0 for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1
        return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate rule-based semantic merge candidates.")
    parser.add_argument("--input", type=Path, default=INPUT_PATH, help="Input labeled_raw_questions JSONL path.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Output merge_candidates.json path.")
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


def tokenize_text(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(token.lower() for token in ASCII_TOKEN_RE.findall(lowered))
    for span in CJK_SPAN_RE.findall(text):
        if len(span) <= 4:
            tokens.add(span)
        for idx in range(len(span) - 1):
            tokens.add(span[idx:idx + 2])
        if len(span) >= 3:
            for idx in range(len(span) - 2):
                tokens.add(span[idx:idx + 3])
    return {token for token in tokens if token}


def build_question_record(row: dict[str, Any]) -> QuestionRecord:
    raw_id = str(row.get("raw_id", "")).strip()
    if not raw_id:
        raise ValueError("Encountered row without raw_id")
    raw_question = str(row.get("raw_question", "")).strip()
    normalized_question = str(row.get("normalized_question", "")).strip()
    category = [str(item).strip() for item in row.get("category", []) if str(item).strip()]
    tags = [str(item).strip().lower() for item in row.get("tags", []) if str(item).strip()]
    tokens = tokenize_text(normalized_question) | tokenize_text(raw_question)
    keywords = set(tags) | tokens
    return QuestionRecord(
        raw_id=raw_id,
        raw_question=raw_question,
        normalized_question=normalized_question,
        category=category,
        tags=tags,
        difficulty=str(row.get("difficulty", "")).strip(),
        job_level=[str(item).strip() for item in row.get("job_level", []) if str(item).strip()],
        confidence=float(row.get("confidence", 0.0)),
        company=str(row.get("company", "")).strip(),
        department=str(row.get("department", "")).strip(),
        post_type=str(row.get("post_type", "")).strip(),
        source_title=str(row.get("source_title", "")).strip(),
        tokens=tokens,
        keywords=keywords,
    )


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def pair_reasons(left: QuestionRecord, right: QuestionRecord) -> list[str]:
    left_categories = set(left.category)
    right_categories = set(right.category)
    left_tags = set(left.tags)
    right_tags = set(right.tags)

    category_overlap = len(left_categories & right_categories)
    tag_overlap = len(left_tags & right_tags)
    keyword_overlap = len(left.keywords & right.keywords)
    token_jaccard = jaccard(left.tokens, right.tokens)
    tag_jaccard = jaccard(left_tags, right_tags)
    text_similarity = SequenceMatcher(None, left.normalized_question, right.normalized_question).ratio()

    reasons: list[str] = []
    if left.normalized_question and left.normalized_question == right.normalized_question:
        return ["exact_normalized_question"]

    if category_overlap > 0 and text_similarity >= 0.92:
        reasons.append("very_high_text_similarity")
    if category_overlap > 0 and tag_overlap >= 2 and (text_similarity >= 0.68 or token_jaccard >= 0.45):
        reasons.append("shared_category_and_multiple_tags")
    if category_overlap > 0 and tag_overlap >= 1 and text_similarity >= 0.78 and keyword_overlap >= 2:
        reasons.append("shared_category_tag_and_text")
    if category_overlap > 0 and tag_jaccard >= 0.60 and keyword_overlap >= 3:
        reasons.append("high_tag_overlap_with_shared_keywords")
    if tag_overlap >= 2 and keyword_overlap >= 3 and token_jaccard >= 0.40:
        reasons.append("shared_tags_and_core_keywords")

    # Guardrail: do not merge solely because category is broad.
    if not reasons:
        return []
    if category_overlap == 0:
        return []
    if tag_overlap == 0 and text_similarity < 0.92:
        return []
    return reasons


def initial_bucket_keys(record: QuestionRecord) -> set[str]:
    keys: set[str] = set()
    categories = record.category or ["UNCATEGORIZED"]
    tags = record.tags or ["untagged"]
    for category in categories:
        for tag in tags:
            keys.add(f"{category}|{tag}")
    if len(tags) >= 2:
        tag_pair = "|".join(sorted(tags[:2]))
        for category in categories:
            keys.add(f"{category}|PAIR|{tag_pair}")
    return keys


def split_large_component(records: list[QuestionRecord]) -> list[list[QuestionRecord]]:
    if len(records) <= 20:
        return [sorted(records, key=lambda item: item.raw_id)]

    exact_map: dict[str, list[QuestionRecord]] = defaultdict(list)
    for record in records:
        exact_map[record.normalized_question].append(record)
    if len(exact_map) > 1:
        result: list[list[QuestionRecord]] = []
        for bucket in sorted(exact_map.values(), key=lambda items: (items[0].normalized_question, items[0].raw_id)):
            result.extend(split_large_component(bucket))
        return result

    signature_map: dict[str, list[QuestionRecord]] = defaultdict(list)
    for record in records:
        signature = "|".join(record.category[:1] + sorted(record.tags)[:2]) or "fallback"
        signature_map[signature].append(record)
    if len(signature_map) > 1:
        result = []
        for bucket in sorted(signature_map.values(), key=lambda items: (items[0].normalized_question, items[0].raw_id)):
            result.extend(split_large_component(bucket))
        return result

    first_tag_map: dict[str, list[QuestionRecord]] = defaultdict(list)
    for record in records:
        first_tag_map[record.tags[0] if record.tags else "untagged"].append(record)
    if len(first_tag_map) > 1:
        result = []
        for bucket in sorted(first_tag_map.values(), key=lambda items: (items[0].normalized_question, items[0].raw_id)):
            result.extend(split_large_component(bucket))
        return result

    sorted_records = sorted(records, key=lambda item: (item.normalized_question, item.raw_id))
    return [sorted_records[idx:idx + 20] for idx in range(0, len(sorted_records), 20)]


def summarize_group_reason(group_records: list[QuestionRecord], edge_reason_map: dict[frozenset[str], set[str]]) -> str:
    reasons: set[str] = set()
    raw_ids = [record.raw_id for record in group_records]
    for left, right in combinations(raw_ids, 2):
        reasons.update(edge_reason_map.get(frozenset((left, right)), set()))
    if "exact_normalized_question" in reasons:
        return "exact normalized_question match"
    if "very_high_text_similarity" in reasons:
        return "very high normalized_question similarity within shared category"
    if "shared_category_and_multiple_tags" in reasons:
        return "shared category plus multiple overlapping tags with similar wording"
    if "shared_category_tag_and_text" in reasons:
        return "shared category and tag with similar normalized_question wording"
    if "high_tag_overlap_with_shared_keywords" in reasons or "shared_tags_and_core_keywords" in reasons:
        return "shared category/tags and overlapping core keywords"
    return "shared category/tags and conservative text similarity rules"


def question_to_payload(record: QuestionRecord) -> dict[str, Any]:
    return {
        "raw_id": record.raw_id,
        "raw_question": record.raw_question,
        "normalized_question": record.normalized_question,
        "category": record.category,
        "tags": record.tags,
        "difficulty": record.difficulty,
        "job_level": record.job_level,
        "confidence": record.confidence,
        "company": record.company,
        "department": record.department,
        "post_type": record.post_type,
        "source_title": record.source_title,
    }


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input.resolve())
    non_drop_rows = [row for row in rows if row.get("keep_status") != "drop"]
    records = [build_question_record(row) for row in non_drop_rows]
    records_by_id = {record.raw_id: record for record in records}

    dsu = DisjointSet([record.raw_id for record in records])
    edge_reason_map: dict[frozenset[str], set[str]] = defaultdict(set)

    exact_map: dict[str, list[QuestionRecord]] = defaultdict(list)
    for record in records:
        exact_map[record.normalized_question].append(record)
    for bucket in exact_map.values():
        if len(bucket) < 2:
            continue
        for left, right in combinations(bucket, 2):
            dsu.union(left.raw_id, right.raw_id)
            edge_reason_map[frozenset((left.raw_id, right.raw_id))].add("exact_normalized_question")

    buckets: dict[str, list[QuestionRecord]] = defaultdict(list)
    for record in records:
        for key in initial_bucket_keys(record):
            buckets[key].append(record)

    visited_pairs: set[frozenset[str]] = set()
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        for left, right in combinations(bucket, 2):
            pair_key = frozenset((left.raw_id, right.raw_id))
            if pair_key in visited_pairs:
                continue
            visited_pairs.add(pair_key)
            reasons = pair_reasons(left, right)
            if not reasons:
                continue
            dsu.union(left.raw_id, right.raw_id)
            edge_reason_map[pair_key].update(reasons)

    components: dict[str, list[QuestionRecord]] = defaultdict(list)
    for record in records:
        components[dsu.find(record.raw_id)].append(record)

    candidate_groups: list[dict[str, Any]] = []
    candidate_group_index = 1
    for component_records in sorted(
        components.values(),
        key=lambda items: (min(item.raw_id for item in items), len(items)),
    ):
        if len(component_records) < 2:
            continue
        for subgroup in split_large_component(component_records):
            if len(subgroup) < 2:
                continue
            candidate_groups.append(
                {
                    "candidate_group_id": f"mc_{candidate_group_index:06d}",
                    "group_reason": summarize_group_reason(subgroup, edge_reason_map),
                    "questions": [question_to_payload(record) for record in sorted(subgroup, key=lambda item: item.raw_id)],
                }
            )
            candidate_group_index += 1

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(candidate_groups, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(candidate_groups)} candidate groups to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
