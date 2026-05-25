#!/usr/bin/env python3
"""
discover_nowcoder_search.py - 从牛客搜索发现面经帖子链接
输出: data/nowcoder_discovered_urls.jsonl

使用牛客搜索 API (gw-c.nowcoder.com/api/sparta/pc/search) 翻页，
提取帖子 id、标题、摘要、时间、疑似公司等信息。
搜索结果有两类帖子:
  - 动态帖 (entityType=74): URL = /feed/main/detail/{uuid}
  - 讨论帖: URL = /discuss/{contentId}
momentData.content 已包含正文(可能截断)，作为 fetch 阶段的 fallback。
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

from department_utils import detect_source_department

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
DEFAULT_KEYWORDS = ["agent", "AI Agent", "Agent 开发"]
SEARCH_API = "https://gw-c.nowcoder.com/api/sparta/pc/search"
MAX_PAGES_PER_KEYWORD = 20
REQUEST_DELAY = 2.0

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json; charset=utf-8",
    "Referer": "https://www.nowcoder.com/search/all?query=agent&subType=818",
    "Origin": "https://www.nowcoder.com",
}

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
]

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
OUTPUT = os.path.join(DATA_DIR, "nowcoder_discovered_urls.jsonl")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def extract_company(text: str):
    """从标题/内容中提取公司名"""
    if not text:
        return None
    for pat, name in COMPANY_PATTERNS:
        if pat.search(text):
            return name
    return None


def _decode_html_entities(text: str) -> str:
    if not text:
        return ""
    text = text.replace("&nbsp;", " ")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&amp;", "&")
    text = text.replace("&ensp;", " ")
    text = text.replace("&emsp;", "  ")
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    return text.strip()


def _parse_record(record: dict, keyword: str):
    """将一条 API record 转为统一的输出 dict"""
    data = record.get("data", {})
    content_id = data.get("contentId")
    moment = data.get("momentData")

    # ---------- 动态帖 (entityType=74) ----------
    if moment and moment.get("id"):
        uuid = moment.get("uuid", "")
        post_id = moment["id"]
        title = moment.get("title", "")
        content = _decode_html_entities(moment.get("content", ""))
        desc = _decode_html_entities(moment.get("desc", ""))
        created_at = moment.get("createdAt")
        entity_type = moment.get("entityType")

        post_url = (
            f"https://www.nowcoder.com/feed/main/detail/{uuid}"
            if uuid
            else f"https://www.nowcoder.com/discuss/{post_id}"
        )

        publish_time = ""
        if created_at:
            try:
                dt = datetime.fromtimestamp(created_at / 1000)
                publish_time = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                publish_time = str(created_at)

        company = extract_company(title) or extract_company(desc)
        department = detect_source_department(
            title=title,
            metadata={"snippet": desc},
            content=content,
        )

        return {
            "query": keyword,
            "title": title,
            "url": post_url,
            "post_id": str(post_id),
            "uuid": uuid,
            "entity_type": entity_type,
            "publish_time": publish_time,
            "snippet": desc or content[:300],
            "content_preview": content,
            "suspected_company": company,
            "source_department": department["department"],
            "source_department_confidence": department["confidence"],
            "source_department_evidence": department["evidence"],
        }

    # ---------- 讨论帖 (无 momentData, contentId 为长 ID) ----------
    if content_id and len(str(content_id)) > 10:
        title = record.get("title") or ""
        post_url = f"https://www.nowcoder.com/discuss/{content_id}"
        company = extract_company(title)
        department = detect_source_department(title=title)

        return {
            "query": keyword,
            "title": title,
            "url": post_url,
            "post_id": str(content_id),
            "uuid": "",
            "entity_type": None,
            "publish_time": "",
            "snippet": "",
            "content_preview": "",
            "suspected_company": company,
            "source_department": department["department"],
            "source_department_confidence": department["confidence"],
            "source_department_evidence": department["evidence"],
        }

    return None


def search_page(keyword: str, page: int = 1, debug: bool = False):
    """通过搜索 API 抓取一页搜索结果"""
    payload = {
        "type": "all",
        "query": keyword,
        "page": page,
        "tag": [{"id": 818}],
        "order": "create",
        "gioParams": {
            "searchFrom_var": "搜索页输入框",
            "searchEnter_var": "主站",
        },
    }
    ts = int(time.time() * 1000)
    url = f"{SEARCH_API}?_={ts}"

    if debug:
        print(f"    [DEBUG] POST {url}  page={page}")

    resp = requests.post(url, headers=API_HEADERS, json=payload, timeout=30)
    resp.raise_for_status()

    body = resp.json()
    if body.get("code") != 0:
        print(f"    API 返回错误: code={body.get('code')} msg={body.get('msg')}", file=sys.stderr)
        return [], 0

    data = body.get("data", {})
    records = data.get("records", [])
    total = data.get("total", 0)

    results = []
    for record in records:
        item = _parse_record(record, keyword)
        if item:
            results.append(item)

    return results, total


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="从牛客搜索发现面经帖子")
    parser.add_argument("--debug", action="store_true", help="显示调试信息")
    parser.add_argument(
        "--max-pages", type=int, default=MAX_PAGES_PER_KEYWORD,
        help=f"每个关键词最多抓取页数 (默认 {MAX_PAGES_PER_KEYWORD})",
    )
    parser.add_argument(
        "--keywords", nargs="+", default=DEFAULT_KEYWORDS,
        help="搜索关键词列表",
    )
    parser.add_argument(
        "--delay", type=float, default=REQUEST_DELAY,
        help=f"请求间隔秒数 (默认 {REQUEST_DELAY})",
    )
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    all_results = {}  # post_id -> result

    for keyword in args.keywords:
        print(f"\n搜索关键词: {keyword}")
        keyword_total = 0

        for page in range(1, args.max_pages + 1):
            try:
                results, total = search_page(keyword, page, debug=args.debug)
            except requests.RequestException as e:
                print(f"    第 {page} 页请求失败: {e}", file=sys.stderr)
                break

            if not results:
                print(f"    第 {page} 页: 无结果，停止翻页")
                break

            new_count = 0
            for r in results:
                pid = r["post_id"]
                if pid not in all_results:
                    all_results[pid] = r
                    new_count += 1
            keyword_total += new_count

            print(
                f"    第 {page} 页: {len(results)} 条 ({new_count} 新), "
                f"总条数={total}, 累计={len(all_results)}"
            )

            if new_count == 0:
                break
            if page * 20 >= total:
                break

            time.sleep(args.delay)

    # 按发布时间倒序
    results_list = sorted(
        all_results.values(),
        key=lambda x: x.get("publish_time", ""),
        reverse=True,
    )

    with open(OUTPUT, "w", encoding="utf-8") as f:
        for r in results_list:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n共发现 {len(results_list)} 个不重复帖子 -> {OUTPUT}")

    # 统计
    by_query = {}
    by_company = {}
    by_department = {}
    for r in results_list:
        by_query[r["query"]] = by_query.get(r["query"], 0) + 1
        c = r.get("suspected_company") or "未知"
        by_company[c] = by_company.get(c, 0) + 1
        d = r.get("source_department") or "未知"
        by_department[d] = by_department.get(d, 0) + 1

    print("\n按关键词:")
    for k, v in by_query.items():
        print(f"  {k}: {v}")
    print("\n按公司 (Top 15):")
    for k, v in sorted(by_company.items(), key=lambda x: -x[1])[:15]:
        print(f"  {k}: {v}")
    print("\n按部门/业务线 (Top 15):")
    for k, v in sorted(by_department.items(), key=lambda x: -x[1])[:15]:
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
