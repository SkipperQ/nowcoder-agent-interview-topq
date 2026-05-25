# nowcoder-agent-interview-topq

端到端流水线：从牛客网爬取面试帖，使用 LLM 标注提取 AI Agent 面试题，合并相似问题，生成高频题榜单。

End-to-end pipeline: crawl Nowcoder interview posts, extract and classify AI Agent interview questions using LLM annotation, merge similar questions, and generate top question rankings.

## 快速开始 / Quick Start

### 环境要求

- Python 3.10+
- `pip install requests`（仅爬虫阶段需要）
- LLM API 访问权限（GLM 或 Codex，用于标注/合并阶段）

### 一键运行

```bash
python scripts/run_pipeline.py                # 完整流水线
python scripts/run_pipeline.py --skip-crawl   # 跳过爬虫（使用已有数据）
python scripts/run_pipeline.py --crawl-only   # 仅爬虫
python scripts/run_pipeline.py --extract-only # 仅提取
```

### 分步运行

```bash
# 阶段 1: 爬虫
python scripts/discover_nowcoder_search.py     # 搜索牛客面经帖
python scripts/fetch_nowcoder_posts.py         # 抓取帖子详情
python scripts/clean_nowcoder_posts.py         # 清洗分类帖子

# 阶段 2: 提取
python scripts/extract_questions.py            # 从原始文本提取面试题
python scripts/fix_company_from_title.py       # 修正公司信息

# 阶段 3: LLM 标注
python scripts/make_llm_batches.py             # 生成 LLM 批次
python scripts/run_llm_label_batches.py        # 运行 LLM 标注（GLM）
# 或: python scripts/run_codex_mini_label_batches.py  # (Codex)
python scripts/validate_llm_output.py          # 校验标注结果
python scripts/build_labeled_raw_table.py      # 合并原始数据与标注

# 阶段 4: 合并 + 导出
python scripts/make_merge_candidates.py        # 生成合并候选
python scripts/make_merge_review_batches.py    # 创建合并审查批次
python scripts/run_merge_batches.py            # 运行 LLM 合并决策
python scripts/validate_merge_output.py        # 校验合并结果
python scripts/build_question_metadata.py      # 构建最终元数据
python scripts/apply_manual_question_overrides.py  # 应用人工修正
python scripts/top_questions.py                # 生成 Excel 导出
```

## 流水线架构 / Pipeline Architecture

```
阶段 1: 爬虫 / CRAWL
  discover_nowcoder_search.py  -->  data/nowcoder_discovered_urls.jsonl
  fetch_nowcoder_posts.py      -->  data/nowcoder_fetched_posts.jsonl
  clean_nowcoder_posts.py      -->  data/raw/nowcoder_crawled_interviews.txt

阶段 2: 提取 / EXTRACT
  extract_questions.py         -->  data/interim/raw_questions.jsonl

阶段 3: LLM 标注 / LABEL
  make_llm_batches.py          -->  data/interim/llm_batches/
  run_llm_label_batches.py     -->  data/interim/llm_results/
  build_labeled_raw_table.py   -->  data/interim/labeled_raw_questions.jsonl

阶段 4: 合并 + 导出 / MERGE + EXPORT
  make_merge_candidates.py     -->  data/interim/merge_candidates.json
  run_merge_batches.py         -->  data/interim/merge_results/
  build_question_metadata.py   -->  data/interim/question_metadata.jsonl
  top_questions.py             -->  data/output/top50_*.xlsx
```

## 配置 / Configuration

- `config/taxonomy.yml` — 分类与标签体系（9 大类，70+ 标签）
- `config/manual_question_overrides.yml` — 人工修正（删除/合并）

## 分类体系 / Categories

| 分类 Category | 说明 Description |
|------|------|
| Agent | Agent 架构、ReAct、工具调用、记忆、规划 |
| RAG | 检索增强生成、向量检索、分块、重排序 |
| MCP | Model Context Protocol、工具、Schema、鉴权 |
| Prompt | 提示词工程、系统提示词、上下文工程 |
| Evaluation | 评估指标、数据集、人工评审、幻觉评估 |
| AICoding | AI 编码工具（Cursor、Claude Code、Copilot） |
| LLM | 模型选型、Token 成本、上下文窗口、微调 |
| CLI | Shell Agent、终端执行、沙箱 |
| OpenEnded | 项目经验、技术权衡、业务场景 |

## 数据政策 / Data Policy

本仓库**不包含任何原始数据或受版权保护的内容**。用户需自行运行爬虫流水线生成数据。示例输出格式见 `sample/top10_questions.md`。

This repository contains **no raw data or copyrighted content**. Users must run the crawl pipeline themselves to generate data. See `sample/top10_questions.md` for an example of pipeline output format.

## License

MIT
