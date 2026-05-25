# Pipeline Details

## Overview

The pipeline transforms unstructured interview posts into a structured, categorized database of top interview questions through 4 stages.

## Stage 1: Crawl

Three scripts crawl interview posts from Nowcoder:

1. **discover_nowcoder_search.py** — Searches Nowcoder API with keywords like "Agent", "AI Agent", "Agent 开发". Discovers post URLs with metadata (title, summary, company hints). Output: `data/nowcoder_discovered_urls.jsonl`.

2. **fetch_nowcoder_posts.py** — Fetches each post's HTML, extracts content from `window.__INITIAL_STATE__`, converts HTML to plain text. Output: `data/nowcoder_fetched_posts.jsonl`.

3. **clean_nowcoder_posts.py** — Filters non-interview and non-Agent-related posts. Classifies posts as `real_interview_post`, `summary_post`, or `general_discussion`. Identifies company/department metadata. Output: `data/nowcoder_cleaned_posts.jsonl` + `data/raw/nowcoder_crawled_interviews.txt` (the bridge format).

## Stage 2: Extract

**extract_questions.py** — Parses the `[来源N]` delimited text format. Splits into individual posts, parses metadata lines (`公司:`, `部门:`, etc.), extracts candidate questions using pattern matching (numbered lists, question keywords). Each question gets a unique `raw_id`. Output: `data/interim/source_posts.jsonl` + `data/interim/raw_questions.jsonl`.

**fix_company_from_title.py** — Re-examines post titles to correct or fill in missing company metadata.

## Stage 3: LLM Labeling

**make_llm_batches.py** — Groups raw questions into batches of 40 for LLM processing.

**run_llm_label_batches.py** (or **run_codex_mini_label_batches.py**) — Sends batches to an LLM (GLM-4.7 or GPT-5.4-mini) for annotation. Each question receives:
- `keep_status`: keep / rewrite / drop
- `normalized_question`: standardized question text
- `category`: one of 9 categories (Agent, RAG, MCP, Prompt, etc.)
- `tags`: 1-4 technical tags (e.g., `tool_calling`, `rerank`)
- `difficulty`: Basic / Intermediate / Advanced
- `confidence`: 0-1 score

**validate_llm_output.py** — Validates schema, enum values, and coverage.

**build_labeled_raw_table.py** — Joins raw questions with LLM annotations into a unified table.

## Stage 4: Merge + Export

**make_merge_candidates.py** — Uses rule-based semantic similarity to group potentially duplicate questions.

**run_merge_batches.py** — LLM reviews candidate groups and decides whether to merge. Creates canonical questions from merged groups. Rule: prefer splitting over over-merging.

**build_question_metadata.py** — Builds final question records with Q IDs (Q000001, etc.), aggregates frequency statistics, merges metadata from all sources.

**apply_manual_question_overrides.py** — Applies manual corrections from `config/manual_question_overrides.yml` (drops, merges, tag fixes).

**top_questions.py** — Generates Excel reports:
- Top 50 by total frequency
- Top 50 by real interview frequency
- Top 10 per category
- Top 10 real interview questions per category

## Data Flow

```
data/raw/*.txt
    ↓  extract
data/interim/raw_questions.jsonl
    ↓  LLM label
data/interim/labeled_raw_questions.jsonl
    ↓  merge
data/interim/question_metadata.final.jsonl
    ↓  export
data/output/top50_*.xlsx
```

## Key Design Decisions

- **LLM over regex**: Question labeling and merging use LLM for higher accuracy than pattern matching alone.
- **Conservative merging**: The merge prompt instructs "宁可拆得细一点，也不要过度合并" — prefer splitting over over-merging.
- **Manual override**: The `manual_question_overrides.yml` allows human correction of systematic LLM errors.
- **Pure Python**: No external dependencies for processing scripts (custom XLSX writer included). Only the crawl stage needs `requests`.
