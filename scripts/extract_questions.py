#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
SOURCE_POSTS_PATH = ROOT / "data" / "interim" / "source_posts.jsonl"
RAW_QUESTIONS_PATH = ROOT / "data" / "interim" / "raw_questions.jsonl"
SOURCE_NAME = "牛客"

SOURCE_SPLIT_PATTERN = re.compile(r"(?m)^\[来源(\d+)\]\s*$")
META_PATTERN = re.compile(r"^(公司|公司集团|具体公司|BU|BU置信度|部门|部门置信度|类型|链接)\s*:\s*(.*)$")
QUESTION_LABEL_PATTERN = re.compile(r"^(?:面试官|面试官问|问|Q|Q\d+|Question|问题)\s*[:：]\s*(.+)$", re.IGNORECASE)
NUMBERED_QUESTION_PATTERN = re.compile(r"^\s*(\d{1,3}|[一二三四五六七八九十]+)[\.\、\)]\s*(.+)$")
HEADER_NOISE_PREFIXES = ("公司:", "公司集团:", "具体公司:", "BU:", "BU置信度:", "部门:", "部门置信度:", "类型:", "链接:")
NON_QUESTION_SHORT_LINES = {"实习拷打", "手撕代码", "开放题", "开放问题", "项目拷打", "八股", "项目"}
OPENING_MARKERS = {"开放题", "开放问题"}
QUESTION_KEYWORDS = ("什么", "如何", "怎么", "为何", "为什么", "区别", "作用", "原理", "流程", "场景", "实现", "设计", "优化", "判断")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split source posts and extract candidate interview questions.")
    parser.add_argument("--input-dir", type=Path, default=RAW_DIR, help="Directory containing raw md/txt files.")
    parser.add_argument("--source-posts-output", type=Path, default=SOURCE_POSTS_PATH, help="Output JSONL for parsed source posts.")
    parser.add_argument("--raw-questions-output", type=Path, default=RAW_QUESTIONS_PATH, help="Output JSONL for extracted questions.")
    parser.add_argument("--context-lines", type=int, default=2, help="Number of context lines before/after each question.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser.parse_args()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def read_text_with_fallback(path: Path) -> str:
    raw = path.read_bytes()
    encodings = ["utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16"]
    best_text = None
    best_score = -1
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        score = text.count("[来源") + text.count("公司:") + text.count("链接:") - text.count("\ufffd") * 5
        if score > best_score:
            best_score = score
            best_text = text
    if best_text is None:
        best_text = raw.decode("utf-8", errors="replace")
    return best_text.replace("\r\n", "\n").replace("\r", "\n")


def split_source_post_chunks(text: str) -> list[tuple[int, str]]:
    matches = list(SOURCE_SPLIT_PATTERN.finditer(text))
    chunks: list[tuple[int, str]] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunks.append((int(match.group(1)), text[start:end].strip("\n")))
    return chunks


def parse_metadata_lines(lines: list[str], start_idx: int) -> tuple[dict[str, str], int]:
    metadata = {
        "company": "未知",
        "company_group": "未知",
        "specific_company": "未知",
        "bu": "未知",
        "bu_confidence": "未知",
        "department": "未知",
        "department_confidence": "未知",
        "post_type": "未知",
        "source_url": "",
    }
    idx = start_idx
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            break
        match = META_PATTERN.match(stripped)
        if not match:
            break
        key, value = match.group(1), clean_text(match.group(2))
        if not value:
            value = "未知" if key != "链接" else ""
        if key == "公司":
            metadata["company"] = value
        elif key == "公司集团":
            metadata["company_group"] = value
        elif key == "具体公司":
            metadata["specific_company"] = value
        elif key == "BU":
            metadata["bu"] = value
        elif key == "BU置信度":
            metadata["bu_confidence"] = value
        elif key == "部门":
            metadata["department"] = value
        elif key == "部门置信度":
            metadata["department_confidence"] = value
        elif key == "类型":
            metadata["post_type"] = value
        elif key == "链接":
            metadata["source_url"] = value
        idx += 1
    return metadata, idx


def parse_source_post(chunk: str, source_index: int, source_post_id: str, source_file: str) -> dict:
    lines = chunk.split("\n")
    cursor = 1 if lines and SOURCE_SPLIT_PATTERN.match(lines[0].strip()) else 0

    while cursor < len(lines) and not clean_text(lines[cursor]):
        cursor += 1
    source_title = clean_text(lines[cursor]) if cursor < len(lines) else ""
    if cursor < len(lines):
        cursor += 1

    while cursor < len(lines) and not clean_text(lines[cursor]):
        cursor += 1

    metadata, content_start = parse_metadata_lines(lines, cursor)
    content_lines = lines[content_start:]
    while content_lines and not clean_text(content_lines[0]):
        content_lines.pop(0)
    content = "\n".join(content_lines).strip()

    department = metadata["department"] if metadata["department"] else "未知"
    department_confidence = metadata["department_confidence"] if metadata["department_confidence"] else "未知"
    department_source = "metadata" if department != "未知" else "unknown"

    return {
        "source_post_id": source_post_id,
        "source_index": source_index,
        "source_title": source_title,
        "company": metadata["company"] or "未知",
        "company_group": metadata["company_group"] or "未知",
        "specific_company": metadata["specific_company"] or "未知",
        "bu": metadata["bu"] or "未知",
        "bu_confidence": metadata["bu_confidence"] or "未知",
        "department": department,
        "department_confidence": department_confidence,
        "department_source": department_source,
        "post_type": metadata["post_type"] or "未知",
        "source_url": metadata["source_url"] or "",
        "source": SOURCE_NAME,
        "source_file": source_file,
        "content": content,
    }


def load_source_posts(input_dir: Path) -> list[dict]:
    files = sorted([p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".txt", ".md"}])
    logging.info("Found %d raw files under %s", len(files), input_dir)

    source_posts: list[dict] = []
    global_counter = 1
    for path in files:
        text = read_text_with_fallback(path)
        chunks = split_source_post_chunks(text)
        logging.info("Split %s into %d source posts", path.name, len(chunks))
        rel_path = str(path.relative_to(ROOT)).replace("\\", "/")
        for source_index, chunk in chunks:
            source_post_id = f"nowcoder_{global_counter:06d}"
            source_posts.append(parse_source_post(chunk, source_index, source_post_id, rel_path))
            global_counter += 1
    return source_posts


def is_meta_line(line: str) -> bool:
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in HEADER_NOISE_PREFIXES)


def looks_like_question(text: str) -> bool:
    candidate = clean_text(text)
    if len(candidate) < 4 or len(candidate) > 300:
        return False
    if candidate in NON_QUESTION_SHORT_LINES:
        return False
    if candidate.endswith(("？", "?")):
        return True
    if any(keyword in candidate for keyword in QUESTION_KEYWORDS):
        return True
    return False


def make_context(lines: list[str], idx: int, radius: int) -> str:
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    context_lines: list[str] = []
    for line in lines[start:end]:
        cleaned = clean_text(line)
        if cleaned and not is_meta_line(cleaned):
            context_lines.append(cleaned)
    return "\n".join(context_lines)


def capture_open_question(lines: list[str], start_idx: int) -> tuple[str, int]:
    collected: list[str] = []
    idx = start_idx + 1
    while idx < len(lines):
        cleaned = clean_text(lines[idx])
        if not cleaned:
            if collected:
                break
            idx += 1
            continue
        if is_meta_line(cleaned):
            idx += 1
            continue
        if NUMBERED_QUESTION_PATTERN.match(cleaned) and collected:
            break
        if QUESTION_LABEL_PATTERN.match(cleaned) and collected:
            break
        collected.append(cleaned)
        if cleaned.endswith(("？", "?")):
            idx += 1
            break
        idx += 1
    return clean_text(" ".join(collected)), idx


def iter_candidate_questions(content: str) -> Iterable[tuple[int, str]]:
    lines = content.split("\n")
    idx = 0
    while idx < len(lines):
        stripped = clean_text(lines[idx])
        if not stripped or is_meta_line(stripped):
            idx += 1
            continue

        if stripped in OPENING_MARKERS:
            question, next_idx = capture_open_question(lines, idx)
            if looks_like_question(question):
                yield idx, question
            idx = max(next_idx, idx + 1)
            continue

        labeled = QUESTION_LABEL_PATTERN.match(stripped)
        if labeled:
            question = clean_text(labeled.group(1))
            if looks_like_question(question):
                yield idx, question
            idx += 1
            continue

        numbered = NUMBERED_QUESTION_PATTERN.match(stripped)
        if numbered:
            question = clean_text(numbered.group(2))
            if looks_like_question(question):
                yield idx, question
            idx += 1
            continue

        if looks_like_question(stripped):
            yield idx, stripped
        idx += 1


def build_raw_id(source_post_id: str, question: str) -> str:
    digest = hashlib.md5(f"{source_post_id}|{question}".encode("utf-8")).hexdigest()[:12]
    return f"rq_{digest}"


def extract_questions_from_post(source_post: dict, context_lines: int) -> list[dict]:
    content = source_post.get("content", "")
    lines = content.split("\n")
    seen: set[str] = set()
    questions: list[dict] = []

    for idx, question in iter_candidate_questions(content):
        normalized = clean_text(question)
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        questions.append(
            {
                "raw_id": build_raw_id(source_post["source_post_id"], normalized),
                "raw_question": normalized,
                "source_post_id": source_post["source_post_id"],
                "source_index": source_post["source_index"],
                "source_title": source_post["source_title"],
                "company": source_post["company"],
                "company_group": source_post["company_group"],
                "specific_company": source_post["specific_company"],
                "bu": source_post["bu"],
                "bu_confidence": source_post["bu_confidence"],
                "department": source_post["department"],
                "department_confidence": source_post["department_confidence"],
                "department_source": source_post["department_source"],
                "post_type": source_post["post_type"],
                "source": source_post["source"],
                "source_url": source_post["source_url"],
                "source_file": source_post["source_file"],
                "context": make_context(lines, idx, context_lines),
            }
        )
    return questions


def write_jsonl(path: Path, rows: list[dict]) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    input_dir = args.input_dir.resolve()
    source_posts_output = args.source_posts_output.resolve()
    raw_questions_output = args.raw_questions_output.resolve()

    if not input_dir.exists():
        logging.error("Input directory does not exist: %s", input_dir)
        return 1

    source_posts = load_source_posts(input_dir)
    write_jsonl(source_posts_output, source_posts)
    logging.info("Wrote %d source posts to %s", len(source_posts), source_posts_output)

    raw_questions: list[dict] = []
    for post in source_posts:
        raw_questions.extend(extract_questions_from_post(post, args.context_lines))
    write_jsonl(raw_questions_output, raw_questions)
    logging.info("Wrote %d raw questions to %s", len(raw_questions), raw_questions_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
