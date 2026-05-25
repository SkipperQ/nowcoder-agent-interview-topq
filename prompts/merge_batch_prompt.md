# Task

Merge semantically close interview questions into canonical questions and return only a JSON array.
Do not output explanations, markdown, code fences, or any extra text.

Your goal is not to force-merge a candidate group into one question.
Your goal is:

- merge only when questions test the same core interview point
- split when questions test different points
- preserve every raw question by assigning every raw_id to exactly one canonical item

Important principle:

- 宁可拆得细一点，也不要过度合并。

# Input

The input is a JSON array of candidate groups. Each candidate group contains:

- `candidate_group_id`
- `group_reason`
- `questions`

Each question includes:

- `raw_id`
- `raw_question`
- `normalized_question`
- `category`
- `tags`
- `difficulty`
- `job_level`
- `confidence`
- `company`
- `department`
- `post_type`
- `source_title`

# Output

Return one output item per input candidate group, in the same order, with exactly these fields:

1. `candidate_group_id`
2. `canonical_items`

`canonical_items` must be a JSON array. Each item must contain exactly these fields:

1. `canonical_question`
2. `raw_ids`
3. `category`
4. `tags`
5. `difficulty`
6. `job_level`

# Core Merge Rules

## 1. Merge only when the core interview point is the same

Only merge questions when they are asking about the same core technical idea with only wording differences.

If two questions touch the same broad system but focus on different submodules, they should stay separate.

## 2. Do not merge just because the category is the same

Questions in the same broad category may still be very different.

Examples that must usually stay separate:

- RAG `chunking`
- RAG `rerank`
- RAG `embedding`
- Agent `intent_recognition`
- Agent `query_rewrite`
- Agent `planning`
- Agent `tool_calling`
- SFT
- DPO
- LLM-as-Judge

## 3. If a candidate group contains multiple themes, split it

If a candidate group is broad or noisy, split it into multiple `canonical_items`.

If a question has no real semantic partner, create a standalone canonical item containing only that question.

## 4. Do not over-generalize

Avoid broad canonical questions such as:

- `Agent 系统怎么设计？`
- `RAG 系统怎么设计？`
- `大模型应用怎么落地？`
- `AI 系统如何优化？`

Do not create this kind of broad summary unless all raw questions are truly asking that exact broad topic.

# High-Risk Mistakes To Avoid

These are especially important:

## RAG

Do not merge these into one canonical question unless the raw questions are clearly the same:

- `chunking`
- `rerank`
- `embedding`
- `hybrid_search`
- `hallucination`

If a canonical question is mainly about low-latency / high-concurrency RAG service architecture, do not automatically add `chunking`, `rerank`, or `embedding` unless the canonical question explicitly asks about them.

## Agent

Do not merge these into one canonical question unless the raw questions are clearly the same:

- `intent_recognition`
- `query_rewrite`
- `planning`
- `tool_calling`
- `memory`
- `workflow`
- `skills`
- `multi_agent`

If a canonical question is about agent paradigms or agent patterns, do not automatically turn it into a broad "Agent system design" question.

## Training / Alignment / Evaluation

Do not merge these into one canonical question unless the raw questions are clearly the same:

- `sft`
- `dpo`
- `qlora`
- `judge_model`
- `llm_judge`
- `ablation`
- `eval_dataset`
- `metrics`

SFT, DPO, and LLM-as-Judge should usually stay separate.

# Raw ID Coverage Rules

For each candidate group:

1. Every input raw_id must appear in exactly one canonical item.
2. No raw_id may be dropped.
3. No raw_id may appear more than once.
4. If a question cannot be safely merged, create a standalone canonical item for it.

# Canonical Question Rules

- `canonical_question` must be a non-empty Chinese interview question.
- It must be specific, reusable, and focused.
- It must preserve the actual technical point.
- It should not be broader than the raw questions.
- It should not mention a specific company unless the question itself is company-specific.

Good style examples:

- `Agent 系统中的意图识别模块如何设计？`
- `RAG 系统中的 chunking 策略如何设计？`
- `MCP Server 如何封装存量接口为标准工具？`
- `LLM-as-Judge 评测体系如何设计？`

# Category / Tags / Difficulty Rules

For each canonical item:

- `category` should reflect the actual merged question, not every remotely related category.
- `tags` must be the minimal essential tag set for that canonical question.
- Do not blindly take the union of all tags from the raw questions.
- Usually keep only 2 to 4 tags.

Tag assignment must follow the canonical question, not the whole candidate group.

Bad example:

- canonical question: `如何设计一个低延迟、高并发的 RAG 服务？`
- bad tags: `["low_latency", "high_concurrency", "chunking", "rerank"]`
- better tags: `["low_latency", "high_concurrency", "performance_optimization"]`

Another bad example:

- canonical question: `常见的 Agent 范式有哪些，分别适合什么任务？`
- bad tags: `["agent_patterns", "workflow_orchestration", "planning", "tool_calling"]`
- better tags: `["agent_patterns", "planning", "orchestration"]`

- `difficulty` should take the highest difficulty among its raw_ids:
  - `基础` < `进阶` < `深入`
- `job_level` should be the union of job levels from its raw_ids, deduplicated.

# Output Field Rules

- `candidate_group_id` must exactly match the input candidate group id.
- `canonical_items` must be a non-empty JSON array.
- `canonical_question` must be a non-empty Chinese question sentence.
- `raw_ids` must be a non-empty JSON array.
- `category` must be a non-empty JSON array using only:
  - `Agent`
  - `RAG`
  - `AICoding`
  - `Prompt`
  - `MCP`
  - `CLI`
  - `Evaluation`
  - `LLM`
  - `OpenEnded`
- `tags` must be a non-empty JSON array of lowercase snake_case strings.
- `difficulty` must be one of:
  - `基础`
  - `进阶`
  - `深入`
- `job_level` must be a non-empty JSON array using only:
  - `初级`
  - `中级`
  - `高级`

# Output Format

- Output only a JSON array.
- No prose.
- No comments.
- No markdown fences.
- No extra text before or after the JSON.

# Input Batch

{{BATCH_JSON}}
