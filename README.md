# nowcoder-agent-interview-topq

End-to-end pipeline: crawl Nowcoder interview posts, extract and classify AI Agent interview questions using LLM annotation, merge similar questions, and generate top question rankings.

## Quick Start

### Prerequisites

- Python 3.10+
- `pip install requests` (crawl stage only)
- LLM API access (GLM or Codex, for labeling/merge stages)

### One-command pipeline

```bash
python scripts/run_pipeline.py                # full pipeline
python scripts/run_pipeline.py --skip-crawl   # skip crawling (use existing data)
python scripts/run_pipeline.py --crawl-only   # crawl only
python scripts/run_pipeline.py --extract-only # extract only
```

### Individual stages

```bash
# Stage 1: Crawl
python scripts/discover_nowcoder_search.py     # search Nowcoder for interview posts
python scripts/fetch_nowcoder_posts.py         # fetch post content
python scripts/clean_nowcoder_posts.py         # clean and classify posts

# Stage 2: Extract
python scripts/extract_questions.py            # extract questions from raw text
python scripts/fix_company_from_title.py       # fix company metadata

# Stage 3: LLM Labeling
python scripts/make_llm_batches.py             # create LLM batches
python scripts/run_llm_label_batches.py        # run LLM annotation (GLM)
# or: python scripts/run_codex_mini_label_batches.py  # (Codex)
python scripts/validate_llm_output.py          # validate results
python scripts/build_labeled_raw_table.py      # join raw + labeled data

# Stage 4: Merge + Export
python scripts/make_merge_candidates.py        # generate merge candidates
python scripts/make_merge_review_batches.py    # create merge review batches
python scripts/run_merge_batches.py            # run LLM merge decisions
python scripts/validate_merge_output.py        # validate merge results
python scripts/build_question_metadata.py      # build final metadata
python scripts/apply_manual_question_overrides.py  # apply manual corrections
python scripts/top_questions.py                # generate Excel exports
```

## Pipeline Architecture

```
Stage 1: CRAWL
  discover_nowcoder_search.py  -->  data/nowcoder_discovered_urls.jsonl
  fetch_nowcoder_posts.py      -->  data/nowcoder_fetched_posts.jsonl
  clean_nowcoder_posts.py      -->  data/raw/nowcoder_crawled_interviews.txt

Stage 2: EXTRACT
  extract_questions.py         -->  data/interim/raw_questions.jsonl

Stage 3: LLM LABEL
  make_llm_batches.py          -->  data/interim/llm_batches/
  run_llm_label_batches.py     -->  data/interim/llm_results/
  build_labeled_raw_table.py   -->  data/interim/labeled_raw_questions.jsonl

Stage 4: MERGE + EXPORT
  make_merge_candidates.py     -->  data/interim/merge_candidates.json
  run_merge_batches.py         -->  data/interim/merge_results/
  build_question_metadata.py   -->  data/interim/question_metadata.jsonl
  top_questions.py             -->  data/output/top50_*.xlsx
```

## Configuration

- `config/taxonomy.yml` — Category and tag taxonomy (9 categories, 70+ tags)
- `config/manual_question_overrides.yml` — Manual drop/merge corrections

## Categories

| Category | Description |
|----------|-------------|
| Agent | Agent architecture, ReAct, tool calling, memory, planning |
| RAG | Retrieval augmented generation, vector search, chunking, rerank |
| MCP | Model Context Protocol, tools, schema, auth |
| Prompt | Prompt engineering, system prompts, context engineering |
| Evaluation | Metrics, datasets, human review, hallucination eval |
| AICoding | AI coding tools (Cursor, Claude Code, Copilot) |
| LLM | Model selection, token cost, context window, fine-tuning |
| CLI | Shell agents, terminal execution, sandbox |
| OpenEnded | Project experience, trade-offs, business scenarios |

## Data Policy

This repository contains **no raw data or copyrighted content**. Users must run the crawl pipeline themselves to generate data. See `sample/top10_questions.md` for an example of pipeline output format.

## License

MIT
