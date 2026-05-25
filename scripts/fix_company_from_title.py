"""后处理脚本：从 source_title 中识别并修复 company=未知 的记录。

用法:
    python scripts/fix_company_from_title.py

只修改 data/interim/ 下的文件，不碰 data/raw/。
脚本可重复执行（幂等）。
"""

import json
import sys
from collections import Counter
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
SOURCE_POSTS = ROOT / "data" / "interim" / "source_posts.jsonl"
RAW_QUESTIONS = ROOT / "data" / "interim" / "raw_questions.jsonl"

# ── 公司名称映射: 关键词 -> 标准公司名 ───────────────────────────────────
# 按长度降序排列，优先匹配更长的关键词（如 "海康威视" 优先于 "海康"）
TITLE_KEYWORD_MAP: list[tuple[str, str]] = sorted(
    [
        ("快手", "快手"),
        ("T厂", "腾讯"),
        ("腾讯", "腾讯"),
        ("淘天", "淘天"),
        ("阿里云", "阿里云"),
        ("阿里", "阿里"),
        ("蚂蚁", "蚂蚁"),
        ("小红书", "小红书"),
        ("百度", "百度"),
        ("美团", "美团"),
        ("字节", "字节"),
        ("抖音", "字节"),
        ("飞书", "字节"),
        ("京东", "京东"),
        ("拼多多", "拼多多"),
        ("滴滴", "滴滴"),
        ("Shopee", "Shopee"),
        ("虾皮", "Shopee"),
        ("万类智生", "万类智生"),
        ("影石", "影石"),
        ("Insta360", "影石"),
        ("海天同创", "海天同创"),
        ("南芯", "南芯"),
        ("途虎养车", "途虎养车"),
        ("三七互娱", "三七互娱"),
        ("海康威视", "海康"),
        ("海康", "海康"),
        ("大梦龙途", "大梦龙途"),
        ("作业帮", "作业帮"),
        ("联想", "联想"),
        ("Moka", "Moka"),
        ("MiniMax", "MiniMax"),
        ("米哈游", "米哈游"),
        ("有赞", "有赞"),
        ("高德", "高德"),
        ("飞猪", "飞猪"),
        ("特斯拉", "特斯拉"),
        ("蔚来", "蔚来"),
        ("中国平安", "中国平安"),
        ("卓望数码", "卓望数码"),
        ("炎魂网络", "炎魂网络"),
        ("趣虫科技", "趣虫科技"),
        ("飞渡科技", "飞渡科技"),
        ("大华股份", "大华股份"),
        ("华锐捷", "华锐捷"),
        ("广州oneway", "oneway"),
        ("oneway", "oneway"),
        ("海天AI", "海天同创"),
        ("海天", "海天同创"),
        ("智谱华章", "智谱"),
        ("智谱", "智谱"),
        ("TME", "TME"),
        ("安恒信息", "安恒信息"),
        ("金山云", "金山云"),
        ("米可", "米可"),
        ("众安保险", "众安保险"),
        ("千问", "千问"),
        ("通义", "千问"),
        ("哈啰", "哈啰"),
        ("阶跃星辰", "阶跃星辰"),
    ],
    key=lambda x: len(x[0]),
    reverse=True,
)

# 别名集合（匹配到这些时 confidence=medium）
ALIAS_KEYWORDS = {"T厂"}

# ── 非公司关键词：匹配到时不视为公司 ─────────────────────────────────────
NOT_COMPANY_KEYWORDS = {
    "牛客", "程序员花海", "博主", "知识星球", "分享", "面经",
    "整理", "合集", "攻略", "八股", "总结",
}

# ── 合集/总结类关键词：标题包含这些且无明确公司时保持未知 ────────────────
COLLECTION_KEYWORDS = {
    "整理", "合集", "汇总", "攻略", "八股", "总结",
    "80道", "相关的Agent开发面试题", "知识星球", "博主",
}

# ── company_group 映射 ───────────────────────────────────────────────────
COMPANY_GROUP_MAP: dict[str, str] = {
    "淘天": "阿里",
    "阿里云": "阿里",
    "蚂蚁": "阿里",
    "阿里": "阿里",
    "高德": "阿里",
    "飞猪": "阿里",
    "千问": "阿里",
    "字节": "字节",
    "Shopee": "Shopee",
}


def get_company_group(company: str) -> str:
    return COMPANY_GROUP_MAP.get(company, company)


def is_collection_title(title: str) -> bool:
    return any(kw in title for kw in COLLECTION_KEYWORDS)


def identify_company(title: str) -> tuple[str | None, str]:
    """从标题识别公司。返回 (company, confidence) 或 (None, '')。"""
    # 先排除非公司关键词
    for kw in NOT_COMPANY_KEYWORDS:
        if kw in title:
            # 去掉该关键词后再尝试匹配（避免"面经"干扰"百度面经"）
            pass  # 不直接 return，继续尝试公司匹配

    for keyword, company in TITLE_KEYWORD_MAP:
        if keyword in title:
            confidence = "medium" if keyword in ALIAS_KEYWORDS else "high"
            return company, confidence

    return None, ""


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    # ── 读取 source_posts ────────────────────────────────────────────────
    posts: dict[str, dict] = {}
    with open(SOURCE_POSTS, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            posts[rec["source_post_id"]] = rec

    # ── 统计修复前 ───────────────────────────────────────────────────────
    before_unknown = sum(1 for p in posts.values() if p.get("company") == "未知")

    # ── 修复 source_posts ────────────────────────────────────────────────
    fixed_distribution: Counter[str] = Counter()
    fixed_count = 0

    for post_id, post in posts.items():
        orig_company = post.get("company", "未知")

        # 已有明确公司的记录，保留不动
        if orig_company != "未知":
            if not post.get("company_source"):
                post["company_source"] = "metadata"
                post["company_confidence"] = "high"
            continue

        # company=未知：尝试从标题识别（允许用新增关键词重新匹配）

        # 尝试从标题识别
        identified, confidence = identify_company(post.get("source_title", ""))

        if identified and is_collection_title(post.get("source_title", "")):
            # 合集帖但标题里有明确公司名 → 仍然识别
            pass  # keep identified

        if identified:
            post["company"] = identified
            post["company_group"] = get_company_group(identified)
            post["specific_company"] = identified
            post["company_source"] = "title_rule"
            post["company_confidence"] = confidence
            fixed_count += 1
            fixed_distribution[identified] += 1
        else:
            post["company_source"] = "unknown"
            post["company_confidence"] = "low"

    # ── 写回 source_posts.jsonl ──────────────────────────────────────────
    with open(SOURCE_POSTS, "w", encoding="utf-8") as f:
        for post_id in sorted(posts.keys()):
            f.write(json.dumps(posts[post_id], ensure_ascii=False) + "\n")

    # ── 同步到 raw_questions.jsonl ───────────────────────────────────────
    # 建立 post_id -> company fields 的映射
    post_company_fields: dict[str, dict] = {}
    for post_id, post in posts.items():
        post_company_fields[post_id] = {
            "company": post["company"],
            "company_group": post["company_group"],
            "specific_company": post["specific_company"],
            "company_source": post["company_source"],
            "company_confidence": post["company_confidence"],
        }

    questions: list[dict] = []
    with open(RAW_QUESTIONS, "r", encoding="utf-8") as f:
        for line in f:
            q = json.loads(line)
            pid = q.get("source_post_id")
            if pid and pid in post_company_fields:
                q.update(post_company_fields[pid])
            questions.append(q)

    with open(RAW_QUESTIONS, "w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    # ── 统计修复后 ───────────────────────────────────────────────────────
    after_unknown = sum(1 for p in posts.values() if p.get("company") == "未知")

    # ── 输出统计 ─────────────────────────────────────────────────────────
    print("=" * 50)
    print("fix_company_from_title 统计")
    print("=" * 50)
    print(f"修复前 company=未知 的 source_post 数: {before_unknown}")
    print(f"修复后 company=未知 的 source_post 数: {after_unknown}")
    print(f"通过 title_rule 修复的 source_post 数: {fixed_count}")
    print()
    if fixed_distribution:
        print("修复的公司分布:")
        for company, count in fixed_distribution.most_common():
            print(f"  {company}: {count}")
    print("=" * 50)


if __name__ == "__main__":
    main()
