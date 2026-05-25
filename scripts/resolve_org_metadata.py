#!/usr/bin/env python3
"""
resolve_org_metadata.py - source/question 级组织元信息识别器。

目标是保守抽取四层组织信息：
company_group -> company -> bu -> department。

这个模块先用标题/元信息/正文开头做规则识别；如果将来接 LLM，只需要把
resolve_org_metadata() 中规则未命中的分支替换成窄任务 prompt 调用即可。
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

try:
    from department_utils import (
        UNKNOWN_DEPARTMENT,
        detect_question_department,
        detect_source_department,
        is_known_department,
        normalize_department_name,
    )
except ModuleNotFoundError:
    from .department_utils import (
        UNKNOWN_DEPARTMENT,
        detect_question_department,
        detect_source_department,
        is_known_department,
        normalize_department_name,
    )

UNKNOWN = "未知"
SPECIAL_GROUPS = {"阿里", "腾讯", "字节"}
CONFIDENCE_LEVELS = {"high": 3, "medium": 2, "low": 1}

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DEFAULT_INPUT = os.path.join(DATA_DIR, "nowcoder_cleaned_posts.jsonl")
DEFAULT_OUTPUT = os.path.join(DATA_DIR, "nowcoder_resolved_sources.jsonl")


def clean_text(text) -> str:
    if not text:
        return ""
    text = str(text).replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def compact(text) -> str:
    return re.sub(r"[\s\-_/·|:：,，.。()（）\[\]【】]+", "", clean_text(text))


def is_known(value: str) -> bool:
    return bool(value and value not in {UNKNOWN, "unknown", "Unknown", "None", "null"})


def short_evidence(text: str, limit: int = 120) -> str:
    text = clean_text(text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text[:limit]


def metadata_to_text(metadata) -> str:
    if not metadata:
        return ""
    if isinstance(metadata, str):
        return metadata
    if isinstance(metadata, dict):
        parts = []
        for key, value in metadata.items():
            if isinstance(value, (dict, list, tuple, set)):
                value = metadata_to_text(value)
            if value:
                parts.append(f"{key}: {value}")
        return "\n".join(parts)
    if isinstance(metadata, (list, tuple, set)):
        return "\n".join(metadata_to_text(item) for item in metadata)
    return str(metadata)


def make_result(
    company_group=UNKNOWN,
    company=UNKNOWN,
    bu=UNKNOWN,
    department=UNKNOWN,
    bu_confidence="low",
    bu_source="",
    bu_evidence="",
    department_confidence="low",
    department_source="",
    department_evidence="",
):
    company_group = normalize_company_group(company_group)
    company = normalize_company(company, company_group=company_group)
    bu = normalize_bu(bu, company_group=company_group, company=company)
    department = normalize_org_department(department, evidence=department_evidence, company_group=company_group, bu=bu)
    return {
        "company_group": company_group,
        "company": company,
        "bu": bu,
        "bu_confidence": bu_confidence if bu_confidence in CONFIDENCE_LEVELS else "low",
        "bu_source": bu_source,
        "bu_evidence": short_evidence(bu_evidence),
        "department": department,
        "department_confidence": department_confidence if department_confidence in CONFIDENCE_LEVELS else "low",
        "department_source": department_source,
        "department_evidence": short_evidence(department_evidence),
    }


def normalize_company_group(value: str) -> str:
    value = clean_text(value)
    if not is_known(value):
        return UNKNOWN
    aliases = {
        "阿里巴巴": "阿里",
        "阿里系": "阿里",
        "蚂蚁": "阿里",
        "蚂蚁集团": "阿里",
        "淘天": "阿里",
        "淘宝天猫": "阿里",
        "阿里国际": "阿里",
        "阿里云": "阿里",
        "钉钉": "阿里",
        "字节跳动": "字节",
        "抖音": "字节",
        "飞书": "字节",
        "TikTok": "字节",
        "tiktok": "字节",
        "剪映": "字节",
    }
    return aliases.get(value, value)


def normalize_company(value: str, company_group: str = UNKNOWN) -> str:
    value = clean_text(value)
    if not is_known(value):
        if is_known(company_group):
            return company_group
        return UNKNOWN
    aliases = {
        "阿里巴巴": "阿里",
        "阿里系": "阿里",
        "淘宝天猫": "淘天",
        "淘宝": "淘天",
        "天猫": "淘天",
        "蚂蚁集团": "蚂蚁",
        "字节跳动": "字节",
        "抖音集团": "字节",
        "腾讯云": "腾讯",
        "网易互娱": "网易",
        "bilibili": "B站",
        "哔哩哔哩": "B站",
        "PDD": "拼多多",
        "pdd": "拼多多",
        "miHoYo": "米哈游",
        "minimax": "Minimax",
        "moka": "Moka",
    }
    return aliases.get(value, value)


def normalize_bu(value: str, company_group: str = UNKNOWN, company: str = UNKNOWN) -> str:
    value = clean_text(value)
    if not is_known(value):
        return UNKNOWN
    value = value.strip(" -_/:：,，。")
    aliases = {
        "淘宝天猫": "淘天",
        "淘宝": "淘天",
        "天猫": "淘天",
        "国际支付合规": "国际支付",
        "字节国际支付": "国际支付",
        "小红书基础技术": "基础技术",
        "基础技术可观测": "基础技术",
        "基础技术可观测存储": "基础技术",
        "微信事业群": "WXG",
        "teg": "TEG",
        "Teg": "TEG",
        "wxg": "WXG",
        "csig": "CSIG",
        "pcg": "PCG",
        "ieg": "IEG",
        "cdg": "CDG",
        "mpt": "MPT",
    }
    return aliases.get(value, value)


def normalize_org_department(value: str, evidence: str = "", company_group: str = UNKNOWN, bu: str = UNKNOWN) -> str:
    value = clean_text(value)
    if not is_known(value):
        return UNKNOWN

    value = value.strip(" -_/:：,，。")
    value = re.sub(r"\s+", "", value)
    if is_known(bu) and value == bu:
        return UNKNOWN
    aliases = {
        "agent应用": "Agent应用",
        "Agent应用方向": "Agent应用",
        "agent开发": "Agent开发",
        "Agent开发方向": "Agent开发",
        "AI应用开发": "AI应用",
        "AI应用研发": "AI应用",
        "AI应用": "AI应用",
        "Agent优化": "Agent优化",
        "推荐架构": "推荐架构部",
        "内容生态团队": "内容生态",
        "国际支付合规": "合规",
        "国际支付-合规": "合规",
        "基础技术可观测": "可观测",
        "基础技术可观测存储": "可观测-存储",
        "可观测存储": "可观测-存储",
        "体验效能协同办公团队": "协同办公团队",
    }
    if value in aliases:
        return aliases[value]

    evidence_text = f"{value} {evidence}"
    if re.search(r"agent\s*应用|Agent应用|智能体应用", evidence_text, re.I):
        return "Agent应用"
    if re.search(r"agent\s*开发|Agent开发|智能体开发", evidence_text, re.I):
        return "Agent开发"
    if re.search(r"AI\s*应用|AI应用", value, re.I):
        return "AI应用"
    if re.search(r"合规.{0,8}制裁筛查|制裁筛查", evidence_text):
        return "合规-制裁筛查组"
    if re.search(r"合规", evidence_text) and bu == "国际支付":
        return "合规"
    if re.search(r"可观测.{0,8}存储|存储.{0,8}可观测", evidence_text):
        return "可观测-存储"
    if re.search(r"可观测", evidence_text) and bu == "基础技术":
        return "可观测"

    dept = normalize_department_name(value, evidence)
    return dept if is_known_department(dept) else value


COMPANY_RULES = [
    (re.compile(r"蚂蚁|蚂蚁集团"), ("阿里", "蚂蚁")),
    (re.compile(r"阿里国际"), ("阿里", "阿里国际")),
    (re.compile(r"淘天|淘宝天猫|淘宝|天猫"), ("阿里", "淘天")),
    (re.compile(r"阿里云"), ("阿里", "阿里云")),
    (re.compile(r"钉钉"), ("阿里", "钉钉")),
    (re.compile(r"阿里巴巴|阿里"), ("阿里", "阿里")),
    (re.compile(r"腾讯"), ("腾讯", "腾讯")),
    (re.compile(r"字节跳动|字节|抖音|飞书|TikTok|tiktok|剪映"), ("字节", "字节")),
    (re.compile(r"滴滴"), ("滴滴", "滴滴")),
    (re.compile(r"小红书"), ("小红书", "小红书")),
    (re.compile(r"美团"), ("美团", "美团")),
    (re.compile(r"快手"), ("快手", "快手")),
    (re.compile(r"百度"), ("百度", "百度")),
    (re.compile(r"京东"), ("京东", "京东")),
    (re.compile(r"网易|网易互娱"), ("网易", "网易")),
    (re.compile(r"华为"), ("华为", "华为")),
    (re.compile(r"携程"), ("携程", "携程")),
    (re.compile(r"Shopee", re.I), ("Shopee", "Shopee")),
    (re.compile(r"Minimax", re.I), ("Minimax", "Minimax")),
    (re.compile(r"Moka", re.I), ("Moka", "Moka")),
    (re.compile(r"米哈游|miHoYo", re.I), ("米哈游", "米哈游")),
    (re.compile(r"拼多多|PDD", re.I), ("拼多多", "拼多多")),
    (re.compile(r"bilibili|B站|哔哩哔哩", re.I), ("B站", "B站")),
    (re.compile(r"OPPO", re.I), ("OPPO", "OPPO")),
    (re.compile(r"vivo", re.I), ("vivo", "vivo")),
    (re.compile(r"深信服"), ("深信服", "深信服")),
    (re.compile(r"有赞"), ("有赞", "有赞")),
    (re.compile(r"数坤"), ("数坤科技", "数坤科技")),
    (re.compile(r"联想"), ("联想", "联想")),
    (re.compile(r"得物"), ("得物", "得物")),
    (re.compile(r"TP-?Link", re.I), ("TP-Link", "TP-Link")),
    (re.compile(r"Soul", re.I), ("Soul", "Soul")),
]

BU_RULES = [
    ("阿里", re.compile(r"网商"), "网商"),
    ("阿里", re.compile(r"淘天|淘宝天猫"), "淘天"),
    ("阿里", re.compile(r"淘宝闪购"), "淘宝闪购"),
    ("阿里", re.compile(r"阿里国际"), "阿里国际"),
    ("阿里", re.compile(r"阿里云"), "阿里云"),
    ("阿里", re.compile(r"钉钉"), "钉钉"),
    ("阿里", re.compile(r"飞猪"), "飞猪"),
    ("阿里", re.compile(r"高德地图|高德"), "高德地图"),
    ("阿里", re.compile(r"菜鸟"), "菜鸟"),
    ("阿里", re.compile(r"本地生活"), "本地生活"),
    ("阿里", re.compile(r"灵犀互娱"), "灵犀互娱"),
    ("腾讯", re.compile(r"(?<![A-Za-z0-9])WXG(?![A-Za-z0-9])|微信事业群", re.I), "WXG"),
    ("腾讯", re.compile(r"(?<![A-Za-z0-9])CSIG(?![A-Za-z0-9])|云与智慧产业", re.I), "CSIG"),
    ("腾讯", re.compile(r"(?<![A-Za-z0-9])PCG(?![A-Za-z0-9])|平台与内容", re.I), "PCG"),
    ("腾讯", re.compile(r"(?<![A-Za-z0-9])IEG(?![A-Za-z0-9])|互动娱乐", re.I), "IEG"),
    ("腾讯", re.compile(r"(?<![A-Za-z0-9])CDG(?![A-Za-z0-9])", re.I), "CDG"),
    ("腾讯", re.compile(r"(?<![A-Za-z0-9])TEG(?![A-Za-z0-9])|技术工程事业群", re.I), "TEG"),
    ("腾讯", re.compile(r"腾讯云"), "腾讯云"),
    ("腾讯", re.compile(r"腾讯新闻"), "腾讯新闻"),
    ("字节", re.compile(r"国际支付"), "国际支付"),
    ("字节", re.compile(r"飞书"), "飞书"),
    ("字节", re.compile(r"抖音"), "抖音"),
    ("字节", re.compile(r"TikTok", re.I), "TikTok"),
    ("字节", re.compile(r"剪映"), "剪映"),
    ("字节", re.compile(r"基础技术"), "基础技术"),
    ("字节", re.compile(r"商业化|商业技术"), "商业化"),
    ("字节", re.compile(r"电商"), "电商"),
    ("字节", re.compile(r"生活服务"), "生活服务"),
    ("字节", re.compile(r"风控"), "风控"),
    ("字节", re.compile(r"(?<![A-Za-z0-9])AIDP(?![A-Za-z0-9])", re.I), "AIDP"),
    ("滴滴", re.compile(r"(?<![A-Za-z0-9])MPT(?![A-Za-z0-9])", re.I), "MPT"),
    ("小红书", re.compile(r"基础技术"), "基础技术"),
    ("小红书", re.compile(r"内容生态"), "内容生态"),
    ("小红书", re.compile(r"体验效能"), "体验效能"),
    ("小红书", re.compile(r"商业技术"), "商业技术"),
    ("小红书", re.compile(r"社区"), "社区"),
]

DEPARTMENT_RULES = [
    (re.compile(r"agent\s*应用|Agent应用|智能体应用", re.I), "Agent应用"),
    (re.compile(r"agent\s*开发|Agent开发|智能体开发", re.I), "Agent开发"),
    (re.compile(r"AI\s*应用(?:开发|研发)?|AI应用(?:开发|研发)?", re.I), "AI应用"),
    (re.compile(r"合规.{0,8}制裁筛查|制裁筛查"), "合规-制裁筛查组"),
    (re.compile(r"合规"), "合规"),
    (re.compile(r"可观测.{0,8}存储|可观测存储"), "可观测-存储"),
    (re.compile(r"可观测"), "可观测"),
    (re.compile(r"内容生态"), "内容生态"),
    (re.compile(r"内容安全"), "内容安全"),
    (re.compile(r"协同办公"), "协同办公团队"),
    (re.compile(r"推荐架构部|推荐架构"), "推荐架构部"),
    (re.compile(r"搜索"), "搜索"),
    (re.compile(r"广告"), "广告"),
    (re.compile(r"供应链"), "供应链"),
    (re.compile(r"智能驾驶"), "智能驾驶"),
    (re.compile(r"自动驾驶"), "自动驾驶"),
    (re.compile(r"基础架构"), "基础架构"),
    (re.compile(r"增长"), "增长"),
    (re.compile(r"中间件"), "中间件"),
    (re.compile(r"效率工程"), "效率工程"),
    (re.compile(r"智能体平台"), "智能体平台"),
    (re.compile(r"大模型应用"), "大模型应用"),
    (re.compile(r"AI\s*平台|AI平台", re.I), "AI平台"),
]


def detect_company(text: str):
    for pat, (group, company) in COMPANY_RULES:
        m = pat.search(text)
        if m:
            return group, company, short_evidence(text[max(0, m.start() - 20): m.end() + 40])
    return UNKNOWN, UNKNOWN, ""


def detect_bu(text: str, group: str, company: str):
    for target_group, pat, bu in BU_RULES:
        if target_group != group and target_group != company:
            continue
        m = pat.search(text)
        if m:
            return bu, short_evidence(text[max(0, m.start() - 20): m.end() + 40])
    return UNKNOWN, ""


def detect_department_rule(text: str, group: str = UNKNOWN, company: str = UNKNOWN, bu: str = UNKNOWN):
    for pat, dept in DEPARTMENT_RULES:
        m = pat.search(text)
        if m:
            normalized = normalize_org_department(dept, text, company_group=group, bu=bu)
            return normalized, short_evidence(text[max(0, m.start() - 20): m.end() + 40])
    return UNKNOWN, ""


def split_department_into_bu(department: str, group: str, company: str):
    """兼容上一轮 department 字段中含 BU-部门的情况。"""
    department = clean_text(department)
    if not is_known(department):
        return UNKNOWN, UNKNOWN
    if group == "阿里":
        for prefix in ["网商", "淘天", "淘宝闪购", "阿里国际", "阿里云", "高德地图", "灵犀互娱", "钉钉", "飞猪"]:
            if department == prefix:
                return prefix, UNKNOWN
            if department.startswith(prefix + "-"):
                return prefix, department.split("-", 1)[1]
    if group == "字节":
        for prefix in ["国际支付", "飞书", "抖音", "TikTok", "剪映", "基础技术", "商业化", "电商", "生活服务"]:
            if department == prefix:
                return prefix, UNKNOWN
            if department.startswith(prefix + "-"):
                return prefix, department.split("-", 1)[1]
    if group == "腾讯":
        for prefix in ["WXG", "CSIG", "PCG", "IEG", "CDG", "TEG", "腾讯云", "腾讯新闻"]:
            if department == prefix:
                return prefix, UNKNOWN
            if department.startswith(prefix + "-"):
                return prefix, department.split("-", 1)[1]
    if company == "滴滴" and department.startswith("MPT"):
        if department == "MPT":
            return "MPT", UNKNOWN
        return "MPT", department.split("-", 1)[1] if "-" in department else UNKNOWN
    return UNKNOWN, department


def source_slices(title: str, metadata=None, content: str = ""):
    metadata_text = metadata_to_text(metadata)
    content = content or ""
    return [
        ("title", title or "", "high"),
        ("metadata", metadata_text, "medium"),
        ("body", content[:1600], "medium"),
        ("body", content[:5000], "low"),
    ]


def resolve_org_metadata(title: str = "", metadata=None, content: str = "", fallback_company: str = "", fallback_department: str = "") -> dict:
    """解析 source/post 级组织元信息。"""
    group = UNKNOWN
    company = UNKNOWN
    bu = UNKNOWN
    department = UNKNOWN
    bu_source = ""
    bu_evidence = ""
    bu_confidence = "low"
    dept_source = ""
    dept_evidence = ""
    dept_confidence = "low"

    title = title or ""
    combined_text = "\n".join(part for _source, part, _confidence in source_slices(title, metadata, content) if part)

    # 1. 公司集团/具体公司优先从标题识别，随后元信息/正文开头补充。
    for source, text, confidence in source_slices(title, metadata, content):
        if not text:
            continue
        detected_group, detected_company, _evidence = detect_company(text)
        if is_known(detected_group):
            group, company = detected_group, detected_company
            break

    if not is_known(group) and fallback_company:
        detected_group, detected_company, _ = detect_company(fallback_company)
        if is_known(detected_group):
            group, company = detected_group, detected_company
        else:
            company = normalize_company(fallback_company)
            group = normalize_company_group(company)

    # 2. BU 从标题优先识别。
    for source, text, confidence in source_slices(title, metadata, content):
        if not text:
            continue
        detected_bu, evidence = detect_bu(text, group, company)
        if is_known(detected_bu):
            bu = detected_bu
            bu_source = source
            bu_confidence = confidence
            bu_evidence = evidence or text
            break

    # 3. 兼容老 department 字段里包含 BU-部门的路径。
    split_bu, split_dept = split_department_into_bu(fallback_department, group, company)
    if not is_known(bu) and is_known(split_bu):
        bu = split_bu
        bu_source = "metadata"
        bu_confidence = "medium"
        bu_evidence = fallback_department
    if is_known(split_dept):
        department = split_dept
        dept_source = "metadata"
        dept_confidence = "medium"
        dept_evidence = fallback_department

    # 4. Department 从标题/元信息/正文开头识别。
    for source, text, confidence in source_slices(title, metadata, content):
        if not text or is_known(department):
            continue
        detected_dept, evidence = detect_department_rule(text, group=group, company=company, bu=bu)
        if is_known(detected_dept):
            department = detected_dept
            dept_source = source
            dept_confidence = confidence
            dept_evidence = evidence or text
            break

    if not is_known(department):
        old = detect_source_department(title=title, metadata=metadata, content=content)
        if is_known_department(old.get("department")):
            old_bu, old_dept = split_department_into_bu(old["department"], group, company)
            if not is_known(bu) and is_known(old_bu):
                bu = old_bu
                bu_source = old.get("source") or "title"
                bu_confidence = old.get("confidence") or "low"
                bu_evidence = old.get("evidence") or old["department"]
            department = old_dept if is_known(old_dept) else old["department"]
            dept_source = old.get("source") or "title"
            dept_confidence = old.get("confidence") or "low"
            dept_evidence = old.get("evidence") or old["department"]

    # 5. 根据强组合做保守修正。
    ctext = compact(combined_text)
    if group == "阿里" and company == "蚂蚁" and "网商" in ctext and not is_known(bu):
        bu, bu_source, bu_confidence, bu_evidence = "网商", "title", "high", title or combined_text
    if group == "字节" and "国际支付" in ctext and not is_known(bu):
        bu, bu_source, bu_confidence, bu_evidence = "国际支付", "title", "high", title or combined_text
    if company == "滴滴" and re.search(r"\bMPT\b", combined_text, re.I) and not is_known(bu):
        bu, bu_source, bu_confidence, bu_evidence = "MPT", "title", "high", title or combined_text
    if group == "小红书" and "基础技术" in ctext and not is_known(bu):
        bu, bu_source, bu_confidence, bu_evidence = "基础技术", "title", "high", title or combined_text

    if not is_known(department):
        department = UNKNOWN
    department = normalize_org_department(department, dept_evidence, company_group=group, bu=bu)
    if not is_known(department):
        dept_confidence = "low"
        dept_source = ""
        dept_evidence = ""
    if not is_known(bu):
        bu = UNKNOWN
        bu_confidence = "low"
        bu_source = ""
        bu_evidence = ""

    return make_result(
        company_group=group,
        company=company,
        bu=bu,
        department=department,
        bu_confidence=bu_confidence,
        bu_source=bu_source,
        bu_evidence=bu_evidence,
        department_confidence=dept_confidence,
        department_source=dept_source,
        department_evidence=dept_evidence,
    )


def resolve_question_org(question_text: str = "", context: str = "", source_org: dict = None) -> dict:
    """题目级组织信息：局部强证据优先，缺失时继承 source_org。"""
    source_org = source_org or {}
    source_group = source_org.get("company_group") or source_org.get("source_company_group") or UNKNOWN
    source_company = source_org.get("company") or source_org.get("source_company") or UNKNOWN
    source_bu = source_org.get("bu") or source_org.get("source_bu") or UNKNOWN
    source_dept = source_org.get("department") or source_org.get("source_department") or UNKNOWN

    text = "\n".join(part for part in [question_text or "", context or ""] if part)
    local = resolve_org_metadata(
        title="",
        metadata={},
        content=text,
        fallback_company=source_company,
        fallback_department=source_dept,
    )

    source_group = normalize_company_group(source_group)
    source_company = normalize_company(source_company, source_group)
    source_bu = normalize_bu(source_bu, source_group, source_company)

    # 题目级默认不改写 source 的集团/公司；只有题目文本里出现明确组织词才允许覆盖。
    explicit_group, explicit_company, _ = detect_company(question_text or "")
    has_explicit_company = is_known(explicit_group)

    group = source_group if is_known(source_group) else local["company_group"]
    company = source_company if is_known(source_company) else local["company"]
    if has_explicit_company:
        group = explicit_group
        company = explicit_company

    local_bu = normalize_bu(local.get("bu") or UNKNOWN, group, company)
    bu = source_bu
    bu_conf = source_org.get("bu_confidence") or source_org.get("source_bu_confidence") or "low"
    bu_source = "inherited" if is_known(source_bu) else ""
    bu_evidence = source_org.get("bu_evidence") or source_org.get("source_bu_evidence") or ""
    if is_known(local_bu):
        bu = local_bu
        bu_conf = local.get("bu_confidence", "low")
        bu_source = local.get("bu_source", "") or "body"
        bu_evidence = local.get("bu_evidence", "")
    elif not is_known(bu):
        bu = UNKNOWN

    local_dept = local["department"]
    if is_known(local_dept) and local_dept != source_dept:
        dept = local_dept
        dept_conf = local["department_confidence"]
        dept_source = local["department_source"] or "body"
        dept_evidence = local["department_evidence"]
    elif is_known(source_dept):
        dept = source_dept
        dept_conf = source_org.get("department_confidence") or source_org.get("source_department_confidence") or "low"
        dept_source = "inherited" if dept_conf in {"high", "medium"} else "inferred_from_source"
        dept_evidence = source_org.get("department_evidence") or source_org.get("source_department_evidence") or ""
    else:
        dept_info = detect_question_department(question_text, context, source_dept)
        dept = dept_info.get("department", UNKNOWN)
        dept_conf = dept_info.get("department_confidence", "low")
        dept_source = dept_info.get("department_source", "")
        dept_evidence = dept_info.get("department_evidence", "")

    dept = normalize_org_department(dept, dept_evidence, company_group=group, bu=bu)
    if not is_known(dept):
        dept = UNKNOWN
        dept_conf = "low"
        dept_source = ""
        dept_evidence = ""

    if not is_known(bu):
        bu = UNKNOWN
        bu_conf = "low"
        bu_source = ""
        bu_evidence = ""

    return {
        "company_group": group if is_known(group) else UNKNOWN,
        "company": company if is_known(company) else UNKNOWN,
        "bu": normalize_bu(bu, group, company),
        "bu_confidence": bu_conf if bu_conf in CONFIDENCE_LEVELS else "low",
        "bu_source": bu_source,
        "bu_evidence": short_evidence(bu_evidence),
        "department": dept,
        "department_confidence": dept_conf if dept_conf in CONFIDENCE_LEVELS else "low",
        "department_source": dept_source,
        "department_evidence": short_evidence(dept_evidence),
    }


def summarize_values(values: list, unknown: str = UNKNOWN, top_n: int = 8, key_name: str = "value") -> dict:
    normalized = [v if is_known(v) else unknown for v in values]
    counts = Counter(normalized)
    known_count = sum(c for v, c in counts.items() if v != unknown)
    unknown_count = counts.get(unknown, 0)
    known_values = [v for v, _ in counts.most_common() if v != unknown]
    top_values = [
        {key_name: v, "count": c}
        for v, c in counts.most_common()
        if v != unknown
    ][:top_n]
    if not top_values and unknown_count:
        top_values = [{key_name: unknown, "count": unknown_count}]
    return {
        "values": known_values or ([unknown] if unknown_count else []),
        "known_count": known_count,
        "unknown_count": unknown_count,
        "top": top_values,
    }


def source_org_from_row(row: dict) -> dict:
    return {
        "company_group": row.get("source_company_group") or row.get("company_group") or UNKNOWN,
        "company": row.get("source_company") or row.get("company") or UNKNOWN,
        "bu": row.get("source_bu") or row.get("bu") or UNKNOWN,
        "bu_confidence": row.get("source_bu_confidence") or row.get("bu_confidence") or "low",
        "bu_source": row.get("source_bu_source") or row.get("bu_source") or "",
        "bu_evidence": row.get("source_bu_evidence") or row.get("bu_evidence") or "",
        "department": row.get("source_department") or row.get("department") or UNKNOWN,
        "department_confidence": row.get("source_department_confidence") or row.get("department_confidence") or "low",
        "department_source": row.get("source_department_source") or row.get("department_source") or "",
        "department_evidence": row.get("source_department_evidence") or row.get("department_evidence") or "",
    }


def main():
    parser = argparse.ArgumentParser(description="解析 source 级组织元信息")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if not os.path.exists(args.input):
        print(f"输入文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)

    rows = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            org = resolve_org_metadata(
                title=row.get("title", ""),
                metadata={"author_meta": row.get("author_meta", {}), "query": row.get("query", "")},
                content=row.get("content", ""),
                fallback_company=row.get("company", ""),
                fallback_department=row.get("source_department", ""),
            )
            row.update({
                "source_company_group": org["company_group"],
                "source_company": org["company"],
                "source_bu": org["bu"],
                "source_bu_confidence": org["bu_confidence"],
                "source_bu_evidence": org["bu_evidence"],
                "source_department": org["department"],
                "source_department_confidence": org["department_confidence"],
                "source_department_evidence": org["department_evidence"],
            })
            rows.append(row)

    with open(args.output, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    known_bu = sum(1 for r in rows if is_known(r.get("source_bu")))
    known_dept = sum(1 for r in rows if is_known(r.get("source_department")))
    print(f"解析完成: {len(rows)} source -> {args.output}")
    print(f"source_bu 已识别: {known_bu}")
    print(f"source_department 已识别: {known_dept}")


if __name__ == "__main__":
    main()
