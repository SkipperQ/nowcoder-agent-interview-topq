#!/usr/bin/env python3
"""
department_utils.py - 部门/业务线/团队/方向识别与归一化工具。

规则目标是保守可追溯：只在标题、元信息、正文中有明确线索时给出部门；
否则统一返回 "未知"。
"""

import re
from collections import Counter

UNKNOWN_DEPARTMENT = "未知"
CONFIDENCE_LEVELS = {"high": 3, "medium": 2, "low": 1}


COMPANY_WORDS_RE = re.compile(
    r"阿里云|阿里巴巴|阿里国际|阿里|淘天|淘宝|天猫|蚂蚁|腾讯|字节跳动|字节|抖音|"
    r"小红书|美团|快手|百度|京东|网易|华为|携程|滴滴|高德|深信服|有赞|数坤|"
    r"联想|拼多多|PDD|Shopee|Minimax|Moka|米哈游|bilibili|B站|OPPO|vivo|Soul",
    re.I,
)

NOISE_WORDS_RE = re.compile(
    r"^\s*(?:\d{1,2}\.\d{1,2}|27届?|26届?|28届?|春招|秋招|暑期|日常|校招|社招|"
    r"实习|一面|二面|三面|四面|终面|HR面|技术面|交叉面|加面|电话面|视频面|"
    r"面经|面试|分享|凉经|复盘|攒人品|oc|offer|已offer|笔试|AK|记录|求助)\s*",
    re.I,
)

TRAILING_NOISE_RE = re.compile(
    r"(?:一面|二面|三面|四面|终面|HR面|技术面|交叉面|加面|电话面|视频面|"
    r"面经|面试|分享|凉经|复盘|攒人品|oc|offer|已offer|笔试|AK|"
    r"实习|暑期|日常|校招|社招|秋招|春招|岗位|职位|工程师|实习生|"
    r"面试官|后端开发|前端开发|客户端开发|算法实习|开发实习|研发实习)+$",
    re.I,
)

ROLE_ONLY_RE = re.compile(
    r"^(?:ai|AI)?\s*(?:agent|智能体)?\s*(?:开发|后端|前端|算法|全栈|客户端|测开|测试|"
    r"应用开发|研发|工程师|产品工程师|大模型算法|大模型开发|java|go|python)+$",
    re.I,
)

QUESTION_WORDS_RE = re.compile(
    r"怎么|如何|为什么|什么|有没有|哪些|介绍|说说|讲一下|解释|区别|原理|"
    r"项目|算法题|手撕|八股|反问|自我介绍|实习拷打|项目拷打"
)

FALSE_DEPARTMENT_RE = re.compile(
    r"未知部门|核心组|组成|组合|组件|数组|分组|消费者组|低位组|高位组|多组|两组|几组|一组|"
    r"这个组|这种组|那种组|业务组|重点看|候选人|朋友|面这个部门|职业方向|未来的职业方向|"
    r"资金部门|不同的业务组|高并发本地服务平台|原数组|新数组|DPO需要|Chosen|Rejected",
    re.I,
)

DEPARTMENT_SIGNAL_RE = re.compile(
    r"部门|团队|小组|组|事业群|业务线|中台|平台|方向|架构部|基础技术|推荐架构|"
    r"内容生态|内容安全|体验效能|协同办公|国际支付|合规|制裁筛查|风控|搜索|"
    r"广告|电商|供应链|智能驾驶|自动驾驶|基础架构|可观测|存储|增长|社区|"
    r"商业化|商业技术|AI平台|大模型应用|智能体平台|效率工程|中间件|数据工程|"
    r"云架构|安全|微信|新闻|网商|钉钉|飞猪|剪映|TikTok|AIDP|WXG|Teg|TEG",
    re.I,
)


ALIAS_RULES = [
    (r"国际支付\s*[-_/· ]*\s*合规\s*[-_/· ]*\s*制裁筛查(?:组)?", "国际支付-合规-制裁筛查组"),
    (r"国际支付.{0,8}合规|合规.{0,12}国际支付", "国际支付-合规"),
    (r"国际支付", "国际支付"),
    (r"基础技术.{0,8}可观测.{0,8}存储|可观测.{0,8}存储.{0,8}基础技术", "基础技术-可观测-存储"),
    (r"基础技术.{0,8}可观测", "基础技术-可观测"),
    (r"可观测.{0,8}存储", "可观测-存储"),
    (r"体验效能.{0,8}协同办公(?:团队)?", "体验效能-协同办公团队"),
    (r"协同办公(?:团队)?", "协同办公团队"),
    (r"内容生态(?:团队)?", "内容生态"),
    (r"内容安全", "内容安全"),
    (r"推荐架构(?:部)?", "推荐架构部"),
    (r"Soul.{0,8}推荐架构(?:部)?", "推荐架构部"),
    (r"(?:Teg|TEG|腾讯Teg|腾讯TEG).{0,10}云架构平台部.{0,10}存储业务", "云架构平台部-存储业务"),
    (r"云架构平台部.{0,10}存储业务", "云架构平台部-存储业务"),
    (r"云架构平台部", "云架构平台部"),
    (r"存储业务", "存储业务"),
    (r"风控中台", "风控中台"),
    (r"安全与风控|安全风控", "安全与风控"),
    (r"风控算法|风控相关|风控方向|风控", "风控"),
    (r"\bWXG\b|\bwxg\b", "WXG"),
    (r"AIDP\s*部门|aidp\s*部门", "AIDP部门"),
    (r"AI\s*Agent\s*效率研发|Agent\s*效率研发", "AI Agent效率研发"),
    (r"效能研发", "效能研发"),
    (r"(?:WXG|wxg).{0,6}微信搜索", "WXG-微信搜索"),
    (r"微信搜索", "微信搜索"),
    (r"腾讯新闻.{0,8}数据工程", "腾讯新闻-数据工程"),
    (r"腾讯新闻", "腾讯新闻"),
    (r"数据工程", "数据工程"),
    (r"商业技术", "商业技术"),
    (r"商业化", "商业化"),
    (r"阿里国际.{0,8}风控.{0,8}合规|风控.{0,8}合规", "风控-合规"),
    (r"淘天.{0,8}Agent\s*优化", "淘天-Agent优化"),
    (r"淘天.{0,8}AI\s*应用(?:算法|研发|开发)?", "淘天-AI应用"),
    (r"淘天", "淘天"),
    (r"淘宝闪购.{0,8}AI\s*应用(?:开发|研发)?", "淘宝闪购-AI应用"),
    (r"淘宝闪购", "淘宝闪购"),
    (r"高德地图.{0,8}AI\s*应用(?:开发|研发)?", "高德地图-AI应用"),
    (r"高德地图", "高德地图"),
    (r"阿里云.{0,12}AI\s*Agent\s*平台", "阿里云-AI Agent平台"),
    (r"阿里云.{0,8}AI\s*Coding|阿里云.{0,8}ai\s*coding", "阿里云-AI Coding"),
    (r"阿里云", "阿里云"),
    (r"\bAI\s*Coding\b|\bAICoding\b|ai\s*coding", "AI Coding"),
    (r"钉钉", "钉钉"),
    (r"飞猪", "飞猪"),
    (r"灵犀互娱.{0,8}AI\s*应用(?:开发|研发)?", "灵犀互娱-AI应用"),
    (r"灵犀互娱", "灵犀互娱"),
    (r"网商.{0,8}Agent\s*应用", "网商-Agent应用"),
    (r"网商", "网商"),
    (r"剪映.{0,8}AI\s*应用(?:开发|研发)?", "剪映-AI应用"),
    (r"剪映", "剪映"),
    (r"TikTok|tiktok", "TikTok"),
    (r"推荐算法", "推荐算法"),
    (r"AI\s*平台", "AI平台"),
    (r"智能体平台", "智能体平台"),
    (r"AI\s*Agent\s*平台|Agent\s*平台", "AI Agent平台"),
    (r"AI\s*应用平台", "AI应用平台"),
    (r"平台技术", "平台技术"),
    (r"大模型应用", "大模型应用"),
    (r"效率工程", "效率工程"),
    (r"中间件", "中间件"),
    (r"后端开发组", "后端开发组"),
    (r"客户端工具开发", "客户端工具开发"),
    (r"解决方案数据平台组", "解决方案数据平台组"),
    (r"基础架构", "基础架构"),
    (r"搜索", "搜索"),
    (r"广告", "广告"),
    (r"电商", "电商"),
    (r"供应链", "供应链"),
    (r"智能驾驶", "智能驾驶"),
    (r"自动驾驶", "自动驾驶"),
    (r"增长", "增长"),
    (r"社区", "社区"),
]

ALIAS_PATTERNS = [(re.compile(pattern, re.I), canonical) for pattern, canonical in ALIAS_RULES]

GENERIC_ALIAS_DEPARTMENTS = {
    "搜索", "广告", "电商", "供应链", "智能驾驶", "自动驾驶", "增长", "社区",
    "中间件", "基础架构", "AI平台", "大模型应用", "智能体平台", "效率工程",
    "数据工程", "存储业务", "风控", "商业化", "AI Coding", "阿里云", "淘天",
    "飞猪", "钉钉", "剪映", "TikTok", "WXG", "网商", "灵犀互娱", "高德地图",
    "淘宝闪购", "搜索", "内容安全", "AI Agent平台", "AI应用平台", "广告", "平台技术",
}

EXPLICIT_BODY_SIGNAL_RE = re.compile(
    r"部门|团队|组里|业务线|属于|做的是|主要做|整体是做|业务内容|中台|平台部|事业群",
    re.I,
)

ORG_SUFFIX_PATTERN = re.compile(
    r"([A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff \t\-_/·（）()]{1,38}"
    r"(?:部门|团队|小组|组(?!成|件|合|数|下标)|事业群|业务线|中台|平台部|平台|方向|架构部|研发部|算法部|事业部))",
    re.I,
)

EXPLICIT_PATTERNS = [
    re.compile(r"部门整体\s*(?:是|属于)?\s*做\s*([^。；;，,\n]{2,40})", re.I),
    re.compile(r"这个部门\s*(?:是|属于)?\s*做\s*([^。；;，,\n]{2,40})", re.I),
    re.compile(
        r"(?:部门|团队|组|业务线|岗位|职位|方向)(?:\s*(?:是|为|属于|在|：|:|做的是|主要是|主要负责|负责))"
        r"\s*([^。；;，,\n]{2,50})",
        re.I,
    ),
    re.compile(r"(?:岗位|方向)\s*(?:是|为|：|:)\s*([^。；;，,\n]{2,45})", re.I),
    re.compile(r"组里做的是\s*([^。；;，,\n]{2,45})", re.I),
    re.compile(r"是\s*([^。；;，,\n]{2,35}(?:部门|团队|组|业务线|中台|平台|方向|架构部))\s*的", re.I),
    re.compile(r"([A-Za-z0-9\u4e00-\u9fff\-_/·]{2,40}(?:部|团队|组|业务线|中台|平台|方向))的(?:后端|前端|算法|开发|研发)", re.I),
]


def _clean_spaces(text: str) -> str:
    if not text:
        return ""
    text = str(text)
    text = text.replace("\u3000", " ")
    text = text.replace("—", "-").replace("–", "-").replace("_", "-").replace("/", "-").replace("·", "-")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _short_evidence(text: str, limit: int = 120) -> str:
    text = _clean_spaces(text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text[:limit]


def is_known_department(department: str) -> bool:
    return bool(department and department != UNKNOWN_DEPARTMENT and department.lower() != "unknown")


def normalize_department_name(candidate: str, evidence: str = "") -> str:
    """保守归一化部门名称。"""
    if not candidate:
        return UNKNOWN_DEPARTMENT
    raw = _clean_spaces(candidate)
    evidence = _clean_spaces(evidence)
    raw = raw.strip(" #[]【】()（）:：,，;；。.?？!！")

    combined_before_strip = f"{raw} {evidence}"
    compact_before_strip = re.sub(r"[\s\-_/·]", "", combined_before_strip)
    for pat, canonical in ALIAS_PATTERNS:
        if pat.search(combined_before_strip) or pat.search(compact_before_strip):
            if canonical == "可观测-存储" and re.search(r"基础技术", combined_before_strip):
                return "基础技术-可观测-存储"
            if canonical == "风控" and re.search(r"中台", combined_before_strip):
                return "风控中台"
            return canonical

    raw = COMPANY_WORDS_RE.sub("", raw).strip()
    raw = NOISE_WORDS_RE.sub("", raw).strip()
    raw = TRAILING_NOISE_RE.sub("", raw).strip(" -")
    raw = re.sub(r"^(?:部门|团队|组|业务线|岗位|职位|方向)\s*(?:是|为|属于|在|:|：)?\s*", "", raw)
    raw = re.sub(r"(?:的)?(?:后端|前端|算法|客户端|测试|测开)?(?:开发|研发)?(?:工程师|实习生|岗位|职位)$", "", raw, flags=re.I).strip(" -")
    raw = re.sub(r"\s*-\s*", "-", raw)
    raw = re.sub(r"\s+", "", raw)

    if not raw or raw.lower() in {"unknown", "none", "null"}:
        return UNKNOWN_DEPARTMENT
    if raw in {UNKNOWN_DEPARTMENT, "未知部门"}:
        return UNKNOWN_DEPARTMENT

    compact = raw.replace("-", "")
    combined = f"{raw} {evidence}"
    for pat, canonical in ALIAS_PATTERNS:
        if pat.search(combined) or pat.search(compact):
            if canonical == "可观测-存储" and re.search(r"基础技术", combined):
                return "基础技术-可观测-存储"
            if canonical == "风控" and re.search(r"中台", combined):
                return "风控中台"
            return canonical

    if re.search(r"国际支付", combined) and re.search(r"合规", combined):
        return "国际支付-合规"
    if re.search(r"基础技术", combined) and re.search(r"可观测", combined) and re.search(r"存储", combined):
        return "基础技术-可观测-存储"
    if re.search(r"基础技术", combined) and re.search(r"可观测", combined):
        return "基础技术-可观测"

    # 常见同义后缀归一。
    raw = re.sub(r"内容生态团队$", "内容生态", raw)
    raw = re.sub(r"推荐架构$", "推荐架构部", raw)
    raw = re.sub(r"国际支付合规$", "国际支付-合规", raw)
    raw = re.sub(r"基础技术可观测存储$", "基础技术-可观测-存储", raw)
    raw = re.sub(r"基础技术可观测$", "基础技术-可观测", raw)
    raw = re.sub(r"体验效能协同办公团队?$", "体验效能-协同办公团队", raw)

    return raw or UNKNOWN_DEPARTMENT


def _is_reliable_department(candidate: str, evidence: str = "", explicit: bool = False) -> bool:
    if not is_known_department(candidate):
        return False
    compact = re.sub(r"[\s\-_/·]", "", candidate)
    if len(compact) < 2 or len(compact) > 30:
        return False
    if compact in {"这个部门", "该部门", "部门业务", "团队业务", "业务方向", "职业方向"}:
        return False
    if QUESTION_WORDS_RE.search(candidate):
        return False
    if ROLE_ONLY_RE.match(compact):
        return False
    if FALSE_DEPARTMENT_RE.search(compact) or FALSE_DEPARTMENT_RE.search(candidate):
        return False

    evidence_text = f"{candidate} {evidence}"
    if DEPARTMENT_SIGNAL_RE.search(evidence_text):
        return True
    if explicit and not ROLE_ONLY_RE.match(compact):
        return True
    return False


def _result(department=UNKNOWN_DEPARTMENT, confidence="low", source="", evidence=""):
    return {
        "department": department if is_known_department(department) else UNKNOWN_DEPARTMENT,
        "confidence": confidence if confidence in CONFIDENCE_LEVELS else "low",
        "source": source,
        "evidence": _short_evidence(evidence),
    }


def unknown_result(source: str = ""):
    return _result(UNKNOWN_DEPARTMENT, "low", source, "")


def _candidate_result(candidate: str, evidence: str, source: str, confidence: str, explicit: bool = False):
    normalized = normalize_department_name(candidate, evidence)
    if _is_reliable_department(normalized, evidence, explicit=explicit):
        return _result(normalized, confidence, source, evidence)
    return None


def _extract_by_alias(text: str, source: str, confidence: str):
    if not text:
        return None
    clean = _clean_spaces(text)
    for pat, canonical in ALIAS_PATTERNS:
        m = pat.search(clean)
        if not m:
            compact = re.sub(r"[\s\-_/·]", "", clean)
            m = pat.search(compact)
        if m:
            evidence = clean[max(0, m.start() - 20): m.end() + 40] if m.re.pattern != re.sub(r"[\s\-_/·]", "", clean) else clean
            if source == "body" and canonical in GENERIC_ALIAS_DEPARTMENTS and not EXPLICIT_BODY_SIGNAL_RE.search(evidence):
                continue
            return _candidate_result(canonical, evidence, source, confidence, explicit=True)
    return None


def _extract_by_suffix(text: str, source: str, confidence: str):
    clean = _clean_spaces(text)
    for m in ORG_SUFFIX_PATTERN.finditer(clean):
        cand = m.group(1)
        res = _candidate_result(cand, clean[max(0, m.start() - 20): m.end() + 40], source, confidence, explicit=True)
        if res:
            return res
    return None


def _extract_by_explicit_context(text: str, source: str, confidence: str):
    clean = _clean_spaces(text)
    for pat in EXPLICIT_PATTERNS:
        for m in pat.finditer(clean):
            cand = m.group(1)
            evidence = clean[max(0, m.start() - 20): m.end() + 50]
            res = _candidate_result(cand, evidence, source, confidence, explicit=True)
            if res:
                return res
    return None


def extract_department_from_text(text: str, source: str, confidence: str = "medium", allow_explicit: bool = True):
    """从一段文本里抽取部门。返回 result dict 或 None。"""
    if not text:
        return None
    if source == "body":
        res = _extract_by_alias(text, source, confidence)
        if res:
            return res
        if allow_explicit:
            return _extract_by_explicit_context(text, source, confidence)
        return None
    for extractor in (_extract_by_alias, _extract_by_suffix):
        res = extractor(text, source, confidence)
        if res:
            return res
    if allow_explicit:
        res = _extract_by_explicit_context(text, source, confidence)
        if res:
            return res
    return None


def _metadata_to_text(metadata) -> str:
    if not metadata:
        return ""
    if isinstance(metadata, str):
        return metadata
    if isinstance(metadata, dict):
        parts = []
        for key, value in metadata.items():
            if isinstance(value, (dict, list)):
                parts.append(_metadata_to_text(value))
            elif value:
                parts.append(f"{key}: {value}")
        return "\n".join(parts)
    if isinstance(metadata, (list, tuple, set)):
        return "\n".join(_metadata_to_text(item) for item in metadata)
    return str(metadata)


def detect_source_department(title: str = "", metadata=None, content: str = ""):
    """按标题 -> 元信息 -> 正文开头 -> 正文全文的优先级识别 source_department。"""
    title = title or ""
    metadata_text = _metadata_to_text(metadata)
    content = content or ""

    res = extract_department_from_text(title, "title", "high", allow_explicit=True)
    if res:
        return res

    res = extract_department_from_text(metadata_text, "metadata", "medium", allow_explicit=True)
    if res:
        return res

    intro = content[:1200]
    res = extract_department_from_text(intro, "body", "medium", allow_explicit=True)
    if res:
        return res

    res = extract_department_from_text(content[:5000], "body", "low", allow_explicit=True)
    if res:
        return res

    return unknown_result("source")


def detect_question_department(question_text: str, context: str = "", source_department: str = "",
                               source_confidence: str = "low", source_evidence: str = ""):
    """题目级部门识别。局部上下文优先，无法识别时继承 source_department。"""
    local_text = "\n".join(part for part in [question_text or "", context or ""] if part)
    res = extract_department_from_text(local_text, "body", "medium", allow_explicit=True)
    if res:
        return {
            "department": res["department"],
            "department_confidence": res["confidence"],
            "department_source": res["source"],
            "department_evidence": res["evidence"],
        }

    if is_known_department(source_department):
        conf = source_confidence if source_confidence in CONFIDENCE_LEVELS else "low"
        return {
            "department": source_department,
            "department_confidence": conf,
            "department_source": "inherited" if conf in {"high", "medium"} else "inferred_from_source",
            "department_evidence": source_evidence or f"继承自帖子部门: {source_department}",
        }

    return {
        "department": UNKNOWN_DEPARTMENT,
        "department_confidence": "low",
        "department_source": "",
        "department_evidence": "",
    }


def normalize_department_fields(row: dict) -> dict:
    department = normalize_department_name(row.get("department") or row.get("source_department") or UNKNOWN_DEPARTMENT,
                                           row.get("department_evidence") or row.get("source_department_evidence") or "")
    if not is_known_department(department):
        department = UNKNOWN_DEPARTMENT
    row["department"] = department
    row.setdefault("department_confidence", "low")
    row.setdefault("department_source", "")
    row.setdefault("department_evidence", "")
    return row


def summarize_departments(departments: list, top_n: int = 8) -> dict:
    """聚合题组内的部门统计。"""
    normalized = [
        normalize_department_name(d)
        for d in departments
    ]
    normalized = [d if is_known_department(d) else UNKNOWN_DEPARTMENT for d in normalized]
    counts = Counter(normalized)
    known_count = sum(c for d, c in counts.items() if d != UNKNOWN_DEPARTMENT)
    unknown_count = counts.get(UNKNOWN_DEPARTMENT, 0)
    known_departments = [d for d, _ in counts.most_common() if d != UNKNOWN_DEPARTMENT]
    top_departments = [
        {"department": d, "count": c}
        for d, c in counts.most_common()
        if d != UNKNOWN_DEPARTMENT
    ][:top_n]
    if not top_departments and unknown_count:
        top_departments = [{"department": UNKNOWN_DEPARTMENT, "count": unknown_count}]
    return {
        "departments": known_departments or ([UNKNOWN_DEPARTMENT] if unknown_count else []),
        "known_department_count": known_count,
        "unknown_department_count": unknown_count,
        "top_departments": top_departments,
    }


def choose_stronger_department(current: dict, candidate: dict) -> dict:
    """在两个部门识别结果中选择更强的一项。"""
    if not candidate or not is_known_department(candidate.get("department")):
        return current
    if not current or not is_known_department(current.get("department")):
        return candidate
    cur_score = CONFIDENCE_LEVELS.get(current.get("confidence") or current.get("department_confidence"), 0)
    cand_score = CONFIDENCE_LEVELS.get(candidate.get("confidence") or candidate.get("department_confidence"), 0)
    return candidate if cand_score > cur_score else current
