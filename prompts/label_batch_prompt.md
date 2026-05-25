# Task

Label each record in the input JSON array and return only a JSON array. Do not output explanations, markdown, code fences, or any extra text.

# Input Fields

Each item includes:

- `raw_id`
- `raw_question`
- `company`
- `department`
- `source`
- `source_title`
- `context`

Use `raw_question` as the primary signal. Use `context` only to judge whether the question is too context-dependent or to recover the intended meaning.

# Output

Return one output item per input item, in the same order, with exactly these fields:

1. `raw_id`
2. `keep_status`
3. `drop_reason`
4. `normalized_question`
5. `category`
6. `tags`
7. `difficulty`
8. `job_level`
9. `confidence`

# Rules

- `raw_id`: must exactly match the input.
- `keep_status`: must be `keep`, `rewrite`, or `drop`.
- `drop_reason`:
  - if `keep_status = drop`, must be `non_ai` or `vague`
  - otherwise must be `""`
- `normalized_question`:
  - must be Chinese
  - rewrite into a clean interview-question form
  - if dropped, still provide a readable version when possible
- `category`:
  - must be a non-empty JSON array
  - values must come from: `Agent`, `RAG`, `AICoding`, `Prompt`, `MCP`, `CLI`, `Evaluation`, `LLM`, `OpenEnded`
- `tags`:
  - must be a JSON array with 1 to 4 items
  - lowercase snake_case only
  - use precise technical tags such as `query_rewrite`, `intent_recognition`, `chunking`, `rerank`, `tool_calling`, `context_engineering`, `mcp_server`, `error_handling`, `model_routing`, `shell_agent`
- `difficulty`: must be `基础`, `进阶`, or `深入`
- `job_level`:
  - must be a non-empty JSON array
  - values must come from: `初级`, `中级`, `高级`
- `confidence`: number between 0 and 1

# Decision Policy

- If the question is not related to AI, Agent, RAG, LLM, MCP, AICoding, Prompt, CLI, or Evaluation topics, set `keep_status=drop` and `drop_reason=non_ai`.
- If the question depends too heavily on missing context and cannot stand alone as a reusable interview question, set `keep_status=drop` and `drop_reason=vague`.
- Keep AI engineering questions whenever the technical intent is recoverable.
- Do not invent new categories.
- Do not output empty arrays for `category` or `job_level`.

# Output Format

- Output only a JSON array.
- No prose.
- No comments.
- No markdown fences.

# Input Batch

{{BATCH_JSON}}
