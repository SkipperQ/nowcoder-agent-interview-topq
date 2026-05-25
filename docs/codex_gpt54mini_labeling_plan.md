# Codex `gpt-5.4-mini` 批量标注方案

## 目标

在**不重构现有流程**的前提下，使用 Codex 内的 `gpt-5.4-mini` 完成 `data/interim/llm_batches/*.json` 的批量标注，并输出到：

- `data/interim/llm_results/batch_xxx_result.json`

要求保持以下约束不变：

- 不修改 `data/raw`
- 不修改 `data/interim/llm_batches`
- 输出文件命名保持不变
- 仍然支持断点续跑
- 仍然保留错误日志

---

## 为什么不用“一次性直接跑完 109 个文件”

`data/interim/llm_batches` 一共有 109 个文件，直接在单轮对话里一次性处理，风险较高：

- 单次上下文过大，容易超出稳定处理范围
- 输出长度不可控，容易中途中断
- 失败后不可恢复，重跑成本高
- 结果不易逐批校验，也不利于追踪错误

因此更稳妥的方式是：

- 保持 batch 文件不变
- 在执行时把每个 batch 再拆成更小的子批次
- 每个子批次单独标注、单独校验
- 最后再合并为一个 `batch_xxx_result.json`

---

## 总体方案

新增一条“Codex 本地标注”流程，让 Codex 使用 `gpt-5.4-mini` 对 batch 文件做自动化标注，而不是继续走 GLM API。

这个流程的核心思路是：

1. 读取 `data/interim/llm_batches/*.json`
2. 按文件处理，每个文件内部再拆成小子批次
3. 每个子批次交给 `gpt-5.4-mini` 标注
4. 对模型输出做严格清洗和校验
5. 校验通过后写入中间缓存
6. 全部子批次完成后合并成最终结果文件
7. 失败写入 `data/interim/llm_results/errors.log`

---

## 处理原则

### 1. 文件级 batch 不变

输入仍然是：

- `data/interim/llm_batches/batch_001.json`
- `data/interim/llm_batches/batch_002.json`
- ...

输出仍然是：

- `data/interim/llm_results/batch_001_result.json`
- `data/interim/llm_results/batch_002_result.json`

不修改原 batch 文件内容，不覆盖原文件。

### 2. 子批次拆分

建议每个 batch 文件在执行时按 `5-8` 条题目拆分。

推荐默认值：

- `chunk_size = 8`

如果 `mini` 输出不稳定或单次仍然偏慢，可降低为：

- `chunk_size = 5`

这样做的原因：

- 降低单次推理时长
- 降低输出截断概率
- 降低失败后的重试成本
- 提高断点续跑颗粒度

### 3. 串行或低并发执行

全量 109 个文件建议先用串行执行。

原因：

- 更容易定位问题
- 更利于稳定落盘
- 避免多个失败同时发生时难以恢复

在串行跑通之后，再考虑是否需要轻度并发。

---

## 输出字段规范

每条输出必须严格包含以下字段：

1. `raw_id`
2. `keep_status`
3. `drop_reason`
4. `normalized_question`
5. `category`
6. `tags`
7. `difficulty`
8. `job_level`
9. `confidence`

字段约束：

- `keep_status` 只能是：`keep` / `rewrite` / `drop`
- `category` 必须是数组，元素只能来自：
  - `Agent`
  - `RAG`
  - `AICoding`
  - `Prompt`
  - `MCP`
  - `CLI`
  - `Evaluation`
  - `LLM`
  - `OpenEnded`
- `difficulty` 只能是：
  - `基础`
  - `进阶`
  - `深入`
- `job_level` 必须是数组，元素只能来自：
  - `初级`
  - `中级`
  - `高级`
- `tags` 是细粒度标签（如RAG下的chunking、rerank）
- `confidence` 必须在 `0-1` 之间

判定规则：

- 非 AI / Agent / RAG / LLM / MCP / AICoding 相关问题：
  - `keep_status = drop`
  - `drop_reason = non_ai`
- 太依赖上下文、无法形成通用面试题的问题：
  - `keep_status = drop`
  - `drop_reason = vague`
- AI 工程化问题应保留，例如：
  - LLM 流式响应限流
  - Agent 工具调用超时
  - RAG 检索缓存
  - MCP 工具调用失败降级
  - 向量检索服务稳定性

---

## 结果校验策略

`gpt-5.4-mini` 可以承担主流程，但不能直接盲信输出，必须增加严格校验。

每个子批次输出后要立即检查：

1. 是否能清洗出 JSON 数组
2. 输出数组长度是否与输入一致
3. 每条记录是否包含所有必需字段
4. `raw_id` 是否与输入逐条对齐
5. `keep_status` 是否在合法枚举内
6. `category` 是否为合法数组
7. `difficulty` 是否合法
8. `job_level` 是否为合法数组
9. `tags` 是否符合英文小写下划线格式
10. `confidence` 是否在 `0-1` 范围内

如果校验失败：

- 只重试当前子批次
- 不重跑整个 batch 文件
- 不影响已完成的其他子批次

---

## 断点续跑方案

### 文件级跳过

如果最终结果文件已存在且 JSON 合法，则跳过整个 batch：

- `data/interim/llm_results/batch_001_result.json`

### 子批次级缓存

建议增加中间进度目录，例如：

- `data/interim/llm_results/.progress/`

子批次缓存示例：

- `data/interim/llm_results/.progress/batch_001.chunk_01.json`
- `data/interim/llm_results/.progress/batch_001.chunk_02.json`

这样如果执行中断：

- 已完成的子批次不需要重做
- 下次只继续未完成子批次

这是 `mini` 方案里很重要的一层稳定性保障。

---

## 推荐执行步骤

### 第一阶段：实现 Codex 本地标注模式

目标：

- 在现有脚本基础上新增 `gpt-5.4-mini` 标注模式
- 不改现有目录结构
- 不改下游结果格式

建议：

- 保留现有读取/写出逻辑
- 只替换“调用 GLM”这一层
- 增加 `mini` 的子批次处理

### 第二阶段：先跑 1 到 3 号 batch 做基准

建议先跑：

- `batch_001.json`
- `batch_002.json`
- `batch_003.json`

观察以下指标：

- 单个子批次耗时
- 单个 batch 总耗时
- 校验失败率
- 是否经常需要重试
- `drop/non_ai/vague` 判断是否稳定

### 第三阶段：确定参数

建议初始参数：

- `chunk_size = 8`
- 串行执行
- 开启严格校验
- 保留错误日志

如果发现 `mini` 输出不稳，则调成：

- `chunk_size = 5`

### 第四阶段：增加复判机制

建议对以下记录进入复核队列：

- `confidence < 0.7`
- 校验失败后重试才通过
- `keep_status = drop` 但边界不清晰
- `category` 或 `job_level` 明显异常

复核方式可以后续再定：

- 再用更强模型复判
- 或人工 spot check

### 第五阶段：全量运行 109 个文件

在小样本验证通过后，再跑全量：

- 串行优先
- 保留中间缓存
- 保留 `errors.log`
- 全程不覆盖原始数据

---

## 验收标准

完成后至少应满足：

1. 能处理 `data/interim/llm_batches/*.json`
2. 能生成 `data/interim/llm_results/batch_xxx_result.json`
3. 已有合法结果文件会自动跳过
4. 子批次失败不会影响整个任务继续执行
5. 错误会写入 `data/interim/llm_results/errors.log`
6. 输出能通过严格字段校验
7. 不修改 `data/raw`
8. 不修改 `data/interim/llm_batches`

---

## 风险与结论

### 风险

- `gpt-5.4-mini` 在边界样本上的判断一致性可能不如更强模型
- 如果不做子批次拆分，仍然可能出现输出不完整或不稳定
- 如果不做子批次缓存，中断恢复成本会明显升高

### 结论

这个任务**可以**用 Codex 里的 `gpt-5.4-mini` 做，但推荐的正确姿势不是“直接一次性处理 109 个文件”，而是：

- 小批次
- 严格校验
- 子批次级断点续跑
- 必要时复判

这样可以在成本、速度和稳定性之间取得较好的平衡。

---

## 给新会话的执行指令

新开会话后，可以直接给 Codex 这样的任务描述：

```text
请按 docs/codex_gpt54mini_labeling_plan.md 的方案执行。

目标：
- 使用 Codex 内的 gpt-5.4-mini 完成 data/interim/llm_batches/*.json 的批量标注
- 输出到 data/interim/llm_results/batch_xxx_result.json
- 不修改 data/raw
- 不修改 data/interim/llm_batches

要求：
- 在现有脚本基础上新增或改造为 Codex 本地标注模式
- 每个 batch 文件内部按 5-8 条拆成子批次
- 对模型输出做严格 JSON 清洗和字段校验
- 支持子批次级断点续跑
- 失败写入 data/interim/llm_results/errors.log

先只跑 batch_001 到 batch_003 做验证，确认稳定后再准备全量跑 109 个文件。
```

## 额外执行约束

1. Codex 不要在对话中直接逐个 batch 手工标注。
   请实现一个可重复执行的 Python 自动化脚本，由脚本负责读取 batch、调用模型、校验输出、缓存子批次、合并结果并落盘。

2. 新增脚本名固定为：
   scripts/run_codex_mini_label_batches.py

3. 脚本至少支持以下命令：

   python scripts/run_codex_mini_label_batches.py --dry-run --start 1 --end 3
   python scripts/run_codex_mini_label_batches.py --start 1 --end 3 --chunk-size 8
   python scripts/run_codex_mini_label_batches.py --all --chunk-size 8

4. 每个 batch 输出后，最终结果必须兼容现有校验脚本：

   python scripts/validate_llm_output.py

5. 第一阶段只允许处理 batch_001 到 batch_003。
   跑完后停止，不要自动继续全量。
   等我人工检查结果后，再决定是否全量运行。

6. 人工检查重点：
   - 非 AI 问题是否被 drop
   - 模糊问题是否被 drop 或 rewrite
   - query_rewrite / intent_recognition 是否进入 tags
   - Agent / RAG / MCP / Evaluation 等 category 是否合理
   - JSON 是否能通过 validate_llm_output.py

7. 不要修改：
   - data/raw
   - data/interim/llm_batches
   - 已有下游字段格式

8. 如果要新增 prompt，请放到：
   prompts/label_batch_prompt.md

9. 如果要新增中间缓存，请放到：
   data/interim/llm_results/.progress/

10. 如果某个子批次多次失败，请写入：
    data/interim/llm_results/errors.log
    并继续处理后续子批次，不要中断整个任务。
