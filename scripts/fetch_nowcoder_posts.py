#!/usr/bin/env python3
"""
fetch_nowcoder_posts.py - 抓取牛客帖子详情页正文
输入: data/nowcoder_discovered_urls.jsonl
输出: data/nowcoder_fetched_posts.jsonl

策略:
  1. 尝试从详情页 __INITIAL_STATE__ 提取正文 (讨论帖 /feed/ 有 SSR)
  2. 如果无 SSR 数据，使用 discover 阶段的 content_preview 作为 fallback
  3. 忽略评论、相关推荐、热榜等无关内容
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    print("需要安装 requests: pip install requests", file=sys.stderr)
    sys.exit(1)

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from department_utils import detect_source_department, is_known_department

REQUEST_DELAY = 2.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
INPUT = os.path.join(DATA_DIR, "nowcoder_discovered_urls.jsonl")
OUTPUT = os.path.join(DATA_DIR, "nowcoder_fetched_posts.jsonl")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def extract_initial_state(html: str):
    """从 HTML 中提取 window.__INITIAL_STATE__"""
    m = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*(\{.+?)\s*(?:</script>)",
        html, re.DOTALL,
    )
    if not m:
        return None
    raw = m.group(1)
    depth = 0
    for i, c in enumerate(raw):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[: i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def find_content_data(state: dict):
    """从帖子详情页的 prefetchData 中提取 contentData"""
    prefetch = state.get("prefetchData", {})
    for key in prefetch:
        section = prefetch[key]
        if not isinstance(section, dict):
            continue
        ssr = section.get("ssrCommonData")
        if ssr and isinstance(ssr, dict) and "contentData" in ssr:
            cd = ssr["contentData"]
            # 检查是否有实质内容（排除只有 showMessage 的情况）
            if cd.get("title") or cd.get("content") or cd.get("richText"):
                return cd
    return None


def html_to_text(html_str: str) -> str:
    """将 HTML 转换为可读纯文本"""
    if not html_str:
        return ""
    text = html_str
    # 块级标签 -> 换行
    text = re.sub(r"<br\s*/?\s*>", "\n", text)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"</li>", "\n", text)
    text = re.sub(r"</?[ou]l[^>]*>", "\n", text)
    text = re.sub(r"</?div[^>]*>", "\n", text)
    text = re.sub(r"</?h[1-6][^>]*>", "\n", text)
    text = re.sub(r"</?blockquote[^>]*>", "\n", text)
    text = re.sub(r"</?section[^>]*>", "\n", text)
    # 移除所有剩余标签
    text = re.sub(r"<[^>]+>", "", text)
    # 解码 HTML 实体
    for entity, char in [
        ("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"),
        ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"),
        ("&ensp;", " "), ("&emsp;", "  "),
    ]:
        text = text.replace(entity, char)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
    # 清理空白
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch_post_from_ssr(url: str, debug: bool = False):
    """尝试从详情页 SSR 数据提取正文。成功返回 dict，失败返回 None。"""
    if debug:
        print(f"    [DEBUG] GET {url}")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    state = extract_initial_state(resp.text)
    if not state:
        return None

    content_data = find_content_data(state)
    if not content_data:
        return None

    title = content_data.get("title", "")
    rich_text = content_data.get("richText") or content_data.get("content", "")
    content_text = html_to_text(rich_text)
    create_time = content_data.get("createTime")

    user_brief = content_data.get("userBrief", {})
    author_meta = {
        "nickname": user_brief.get("nickname", ""),
        "education": user_brief.get("educationInfo", ""),
        "major": user_brief.get("secondMajorName", ""),
    }

    publish_time = ""
    if create_time:
        try:
            dt = datetime.fromtimestamp(create_time / 1000)
            publish_time = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            publish_time = str(create_time)

    return {
        "title": title,
        "publish_time": publish_time,
        "author_meta": author_meta,
        "content": content_text,
        "source": "ssr",
    }


def resolve_source_department(entry: dict, result: dict = None):
    """用详情页标题/正文重新识别部门，失败时沿用 discover 阶段结果。"""
    result = result or {}
    detected = detect_source_department(
        title=result.get("title") or entry.get("title", ""),
        metadata={
            "snippet": entry.get("snippet", ""),
            "source_department": entry.get("source_department", ""),
        },
        content=result.get("content") or entry.get("content_preview", ""),
    )
    if is_known_department(detected["department"]):
        return detected
    inherited = entry.get("source_department")
    if is_known_department(inherited):
        return {
            "department": inherited,
            "confidence": entry.get("source_department_confidence", "low"),
            "evidence": entry.get("source_department_evidence", ""),
        }
    return detected


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="抓取牛客帖子详情")
    parser.add_argument("--debug", action="store_true", help="显示调试信息")
    parser.add_argument("--limit", type=int, default=0, help="最多抓取条数 (0=全部)")
    parser.add_argument(
        "--delay", type=float, default=REQUEST_DELAY,
        help=f"请求间隔秒数 (默认 {REQUEST_DELAY})",
    )
    args = parser.parse_args()

    if not os.path.exists(INPUT):
        print(f"输入文件不存在: {INPUT}", file=sys.stderr)
        print("请先运行: python scripts/discover_nowcoder_search.py", file=sys.stderr)
        sys.exit(1)

    entries = []
    with open(INPUT, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    if args.limit > 0:
        entries = entries[: args.limit]

    print(f"共 {len(entries)} 个帖子待抓取")
    os.makedirs(DATA_DIR, exist_ok=True)

    fetched = []
    failed = []

    for i, entry in enumerate(entries):
        url = entry.get("url", "")
        title = entry.get("title", "")
        content_preview = entry.get("content_preview", "")
        title_preview = title[:40] if title else url[:40]

        print(f"  [{i + 1}/{len(entries)}] {title_preview}...")

        result = None

        # 尝试从详情页 SSR 提取
        if url:
            result = fetch_post_from_ssr(url, debug=args.debug)

        if result:
            # SSR 成功
            department = resolve_source_department(entry, result)
            result["url"] = url
            result["query"] = entry.get("query", "")
            result["suspected_company"] = entry.get("suspected_company")
            result["source_department"] = department["department"]
            result["source_department_confidence"] = department["confidence"]
            result["source_department_evidence"] = department["evidence"]
            fetched.append(result)
            if args.debug:
                print(f"    [DEBUG] SSR 正文长度: {len(result.get('content', ''))} 字符")
        elif content_preview:
            # Fallback: 使用 discover 阶段的 content_preview
            department = resolve_source_department(entry)
            fetched.append({
                "url": url,
                "title": title,
                "publish_time": entry.get("publish_time", ""),
                "author_meta": {},
                "content": content_preview,
                "source": "search_preview",
                "query": entry.get("query", ""),
                "suspected_company": entry.get("suspected_company"),
                "source_department": department["department"],
                "source_department_confidence": department["confidence"],
                "source_department_evidence": department["evidence"],
            })
            if args.debug:
                print(f"    [DEBUG] 使用搜索摘要, 长度: {len(content_preview)} 字符")
        else:
            failed.append(url)
            print(f"    Warning: 无法获取正文", file=sys.stderr)

        time.sleep(args.delay)

    # 写出结果
    with open(OUTPUT, "w", encoding="utf-8") as f:
        for r in fetched:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    ssr_count = sum(1 for r in fetched if r.get("source") == "ssr")
    preview_count = sum(1 for r in fetched if r.get("source") == "search_preview")

    print(f"\n抓取完成: {len(fetched)} 成功, {len(failed)} 失败 -> {OUTPUT}")
    print(f"  SSR 详情: {ssr_count}, 搜索摘要: {preview_count}")


if __name__ == "__main__":
    main()
