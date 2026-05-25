#!/usr/bin/env python3
"""
clean_nowcoder_posts.py - 清洗牛客面经帖子
输入: data/nowcoder_fetched_posts.jsonl
输出: data/nowcoder_cleaned_posts.jsonl
      data/raw/nowcoder_crawled_interviews.txt  (可直接进入现有流水线)

功能:
  - 过滤非面经文章
  - 分类标签: real_interview_post / summary_post / general_discussion
  - 识别公司
  - 清洗正文
"""

import argparse
import json
import os
import re
import sys

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from department_utils import UNKNOWN_DEPARTMENT
from resolve_org_metadata import UNKNOWN, is_known, resolve_org_metadata

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw")
INPUT = os.path.join(DATA_DIR, "nowcoder_fetched_posts.jsonl")
OUTPUT = os.path.join(DATA_DIR, "nowcoder_cleaned_posts.jsonl")
RAW_OUTPUT = os.path.join(RAW_DIR, "nowcoder_crawled_interviews.txt")

# ---------------------------------------------------------------------------
# 公司识别 (与 extract_questions.py 保持一致)
# ---------------------------------------------------------------------------
COMPANY_PATTERNS = [
    (re.compile(r"(阿里云|阿里巴巴|淘天|蚂蚁|阿里|天猫|淘宝)"), "阿里系"),
    (re.compile(r"(腾讯)"), "腾讯"),
    (re.compile(r"(字节跳动|字节|抖音)"), "字节跳动"),
    (re.compile(r"(美团)"), "美团"),
    (re.compile(r"(快手)"), "快手"),
    (re.compile(r"(小红书)"), "小红书"),
    (re.compile(r"(滴滴)"), "滴滴"),
    (re.compile(r"(高德)"), "高德"),
    (re.compile(r"(百度)"), "百度"),
    (re.compile(r"(京东)"), "京东"),
    (re.compile(r"(网易)"), "网易"),
    (re.compile(r"(华为)"), "华为"),
    (re.compile(r"(携程)"), "携程"),
    (re.compile(r"(深信服)"), "深信服"),
    (re.compile(r"(有赞)"), "有赞"),
    (re.compile(r"(数坤)"), "数坤科技"),
    (re.compile(r"(联想)"), "联想"),
    (re.compile(r"(Minimax|minimax)", re.I), "Minimax"),
    (re.compile(r"(Shopee|shopee)", re.I), "Shopee"),
    (re.compile(r"(小米)"), "小米"),
    (re.compile(r"(OPPO|oppo)"), "OPPO"),
    (re.compile(r"(vivo)"), "vivo"),
    (re.compile(r"(bilibili|B站|哔哩哔哩)", re.I), "B站"),
]


# ---------------------------------------------------------------------------
# 分类关键词
# ---------------------------------------------------------------------------
REAL_INTERVIEW_PATTERNS = [
    re.compile(r"一面|二面|三面|终面|HR面|技术面|交叉面", re.I),
    re.compile(r"面经|面试", re.I),
    re.compile(r"实习.*面|社招.*面|校招.*面|暑期.*面|日常.*面", re.I),
    re.compile(r"\d+面\b"),
    re.compile(r"oc\b|offer", re.I),
]

SUMMARY_PATTERNS = [
    re.compile(r"总结|汇总|整理|大全|全攻略|备考"),
    re.compile(r"必问|必考|高频|常见|常考"),
    re.compile(r"八股|题库"),
    re.compile(r"面试题\s*\d"),
]

GENERAL_DISCUSSION_PATTERNS = [
    re.compile(r"求助|请问|怎么看|大家觉得"),
    re.compile(r"选择|比较|纠结|Offer比较|选哪个"),
    re.compile(r"薪资|待遇|爆料|薪资爆料"),
    re.compile(r"坑|避雷|体验"),
]

# Agent 相关关键词 (用于判断帖子是否与项目主题相关)
AGENT_KEYWORDS = [
    "agent", "智能体", "ReAct", "RAG", "MCP",
    "langchain", "langgraph", "autogen", "crewai",
    "function calling", "工具调用", "多智能体",
    "multi.?agent", "prompt", "大模型",
]


def classify_post(title: str, content: str) -> str:
    """对帖子进行分类: real_interview_post / summary_post / general_discussion"""
    combined = f"{title} {content[:800]}"

    for pat in REAL_INTERVIEW_PATTERNS:
        if pat.search(combined):
            return "real_interview_post"

    for pat in SUMMARY_PATTERNS:
        if pat.search(combined):
            return "summary_post"

    for pat in GENERAL_DISCUSSION_PATTERNS:
        if pat.search(combined):
            return "general_discussion"

    # 兜底: 正文包含编号问题列表，很可能是面经
    numbered_lines = len(re.findall(r"^[1-9]\d*[\.\、．)]\s*\S", content, re.MULTILINE))
    if numbered_lines >= 3:
        return "real_interview_post"

    return "general_discussion"


def extract_company(text: str):
    """从文本中提取公司名"""
    if not text:
        return None
    for pat, name in COMPANY_PATTERNS:
        if pat.search(text):
            return name
    return None


def is_relevant_post(title: str, content: str) -> bool:
    """判断帖子是否与 Agent/大模型 面试相关"""
    combined = f"{title} {content[:500]}".lower()
    for kw in AGENT_KEYWORDS:
        if re.search(kw, combined, re.I):
            return True
    return False


def clean_content(content: str) -> str:
    """清洗正文: 规范空白、去除噪点"""
    content = re.sub(r"[ \t]+", " ", content)
    content = re.sub(r"\n[ \t]+", "\n", content)
    content = re.sub(r"\n\s*\n\s*\n+", "\n\n", content)
    lines = [line.strip() for line in content.split("\n")]
    return "\n".join(lines).strip()


def resolve_source_org(post: dict, title: str, content: str, fallback_company: str = "") -> dict:
    return resolve_org_metadata(
        title=title,
        metadata={
            "author_meta": post.get("author_meta", {}),
            "query": post.get("query", ""),
            "source_department": post.get("source_department", ""),
        },
        content=content,
        fallback_company=fallback_company or post.get("suspected_company", ""),
        fallback_department=post.get("source_department", ""),
    )


def main():
    parser = argparse.ArgumentParser(description="清洗牛客面经帖子")
    parser.add_argument("--debug", action="store_true", help="显示调试信息")
    parser.add_argument(
        "--include-discussion",
        action="store_true",
        help="保留 general_discussion 类型帖子",
    )
    parser.add_argument(
        "--include-irrelevant",
        action="store_true",
        help="保留与 Agent 无关的帖子",
    )
    args = parser.parse_args()

    if not os.path.exists(INPUT):
        print(f"输入文件不存在: {INPUT}", file=sys.stderr)
        print(
            "请先运行: python scripts/fetch_nowcoder_posts.py",
            file=sys.stderr,
        )
        sys.exit(1)

    # 读取抓取结果
    posts = []
    with open(INPUT, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                posts.append(json.loads(line))

    print(f"共 {len(posts)} 个帖子待清洗")

    cleaned = []
    stats = {
        "real_interview_post": 0,
        "summary_post": 0,
        "general_discussion": 0,
        "filtered_type": 0,
        "filtered_topic": 0,
    }

    for post in posts:
        title = post.get("title", "")
        content = post.get("content", "")
        url = post.get("url", "")

        # 分类
        post_type = classify_post(title, content)

        # 过滤: 是否面经相关
        if post_type == "general_discussion" and not args.include_discussion:
            stats["filtered_type"] += 1
            if args.debug:
                print(f"  [过滤-类型] {title[:50]}")
            continue

        # 过滤: 是否与 Agent 相关
        is_relevant = is_relevant_post(title, content)
        if not is_relevant and not args.include_irrelevant:
            stats["filtered_topic"] += 1
            if args.debug:
                print(f"  [过滤-主题] {title[:50]}")
            continue

        # 提取公司
        company = (
            extract_company(title)
            or extract_company(content[:500])
            or post.get("suspected_company")
        )

        # 清洗正文
        content = clean_content(content)
        org = resolve_source_org(post, title, content, fallback_company=company or "")
        company = org["company"] if is_known(org.get("company")) else company

        cleaned.append(
            {
                "url": url,
                "title": title,
                "publish_time": post.get("publish_time", ""),
                "author_meta": post.get("author_meta", {}),
                "post_type": post_type,
                "company": company,
                "source_company_group": org["company_group"],
                "source_company": org["company"],
                "source_bu": org["bu"],
                "source_bu_confidence": org["bu_confidence"],
                "source_bu_evidence": org["bu_evidence"],
                "source_department": org["department"],
                "source_department_confidence": org["department_confidence"],
                "source_department_evidence": org["department_evidence"],
                "query": post.get("query", ""),
                "content": content,
            }
        )
        stats[post_type] = stats.get(post_type, 0) + 1

    # 写出 JSONL
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        for r in cleaned:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 写出 raw/ 文件，与现有流水线兼容
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(RAW_OUTPUT, "w", encoding="utf-8") as f:
        for i, r in enumerate(cleaned):
            f.write(f"[来源{i + 1}]\n")
            f.write(f"{r['title']}\n")
            if r.get("company"):
                f.write(f"公司: {r['company']}\n")
            f.write(f"公司集团: {r.get('source_company_group') or UNKNOWN}\n")
            f.write(f"具体公司: {r.get('source_company') or UNKNOWN}\n")
            f.write(f"BU: {r.get('source_bu') or UNKNOWN}\n")
            f.write(f"BU置信度: {r.get('source_bu_confidence', 'low')}\n")
            if r.get("source_bu_evidence"):
                f.write(f"BU证据: {r['source_bu_evidence']}\n")
            f.write(f"部门: {r.get('source_department') or UNKNOWN_DEPARTMENT}\n")
            f.write(f"部门置信度: {r.get('source_department_confidence', 'low')}\n")
            if r.get("source_department_evidence"):
                f.write(f"部门证据: {r['source_department_evidence']}\n")
            f.write(f"类型: {r['post_type']}\n")
            if r.get("publish_time"):
                f.write(f"时间: {r['publish_time']}\n")
            if r.get("url"):
                f.write(f"链接: {r['url']}\n")
            f.write(f"\n{r['content']}\n\n")

    # 汇总
    print(f"\n清洗完成: {len(cleaned)} 帖子保留")
    print(f"  过滤 (非面经): {stats['filtered_type']}")
    print(f"  过滤 (非Agent): {stats['filtered_topic']}")
    print(f"\n分类统计:")
    for k in ("real_interview_post", "summary_post", "general_discussion"):
        print(f"  {k}: {stats.get(k, 0)}")
    print(f"\n输出:")
    print(f"  JSONL -> {OUTPUT}")
    print(f"  RAW   -> {RAW_OUTPUT}")

    # 公司分布
    by_company = {}
    by_bu = {}
    by_department = {}
    for r in cleaned:
        c = r.get("source_company") or r.get("company") or "未知"
        by_company[c] = by_company.get(c, 0) + 1
        b = r.get("source_bu") or UNKNOWN
        by_bu[b] = by_bu.get(b, 0) + 1
        d = r.get("source_department") or UNKNOWN_DEPARTMENT
        by_department[d] = by_department.get(d, 0) + 1
    print(f"\n公司分布:")
    for k, v in sorted(by_company.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print(f"\nBU/业务线分布 (Top 20):")
    for k, v in sorted(by_bu.items(), key=lambda x: -x[1])[:20]:
        print(f"  {k}: {v}")
    print(f"\n部门/业务线分布 (Top 20):")
    for k, v in sorted(by_department.items(), key=lambda x: -x[1])[:20]:
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
