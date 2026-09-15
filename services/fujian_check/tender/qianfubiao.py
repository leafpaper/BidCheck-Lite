"""投标须知前附表（第2章第1节）解析 + 硬值抽取。"""
from __future__ import annotations

import re

from services.fujian_check.models import HardValues, JVRule, Location, Money, ParsedDoc, QfbRow, Section
from services.fujian_check.tender._tables import parse_numbered_table, row_location
from services.fujian_check.textnorm import money, to_halfwidth

SECTION_LABEL = "第2章第1节 投标须知前附表"


def parse_qianfubiao(doc: ParsedDoc, section: Section) -> list[QfbRow]:
    pages = list(range(section.page_start, section.page_end + 1))
    rows = parse_numbered_table(doc, pages)
    out: list[QfbRow] = []
    for r in rows:
        out.append(QfbRow(item_no=r.item_no, clause=r.clause, name=r.name, content=r.content,
                          loc=row_location(r, SECTION_LABEL, clause_prefix="前附表第")))
    return out


# ---------- 硬值 ----------

_DUR_RE = re.compile(r"总工期[为:：]?\s*(\d{2,4})\s*(?:个)?日历天")
_DUR_ANY_RE = re.compile(r"(\d{2,4})\s*(?:个)?日历天")
_QUOTA_RE = re.compile(r"定额工期[^\d]{0,6}(\d{2,4})")
_VALID_RE = re.compile(r"(\d{2,3})\s*(?:个)?(?:日历天|天|日)")
_MONEY_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(万元|万|元)")
_JV_RE = re.compile(r"(不)?\s*接受\s*联合体")
_JV_N_RE = re.compile(r"(?:不超过|不得超过|最多|成员)[^\d]{0,6}(\d)\s*家")
_PERF_RE = re.compile(r"(\d{1,2})\s*%")


def _find_row(rows: list[QfbRow], *keys: str, clause: str | None = None) -> QfbRow | None:
    if clause:
        for r in rows:
            if r.clause == clause or r.clause.split("/")[0] == clause:
                return r
    for r in rows:
        if any(k in r.name for k in keys):
            return r
    return None


def _set(hard: HardValues, field: str, value, row: QfbRow | None):
    setattr(hard, field, value)
    if row is not None:
        hard.sources[field] = row.loc


def extract_hard_values(rows: list[QfbRow], doc: ParsedDoc | None = None) -> HardValues:
    hard = HardValues()

    r = _find_row(rows, "计划工期", "工期", clause="2.1")
    if r:
        m = _DUR_RE.search(r.content) or _DUR_ANY_RE.search(r.content)
        if m:
            _set(hard, "duration_days", int(m.group(1)), r)
        m = _QUOTA_RE.search(r.content)
        if m:
            _set(hard, "quota_duration_days", int(m.group(1)), r)

    r = _find_row(rows, "质量要求", "质量标准", clause="2.2")
    if r:
        _set(hard, "quality", r.content.strip(), r)

    r = _find_row(rows, "投标有效期", clause="17.1")
    if r:
        m = _VALID_RE.search(r.content)
        if m:
            _set(hard, "validity_days", int(m.group(1)), r)

    # 保证金：跨页时金额可能被 pdfplumber 并进相邻行，故在相邻行里一起找
    r = _find_row(rows, "投标保证金", clause="18.1")
    cand_rows = [x for x in rows if x is r or abs(x.item_no - (r.item_no if r else -99)) <= 1] if r else rows
    for cr in cand_rows:
        t = to_halfwidth(cr.content)
        m = re.search(r"投标保证金金额[^\d]{0,12}(\d[\d,]*(?:\.\d+)?)\s*(万元|万|元)", t) or \
            re.search(r"保证金[^\d。]{0,20}?金额[^\d]{0,12}(\d[\d,]*(?:\.\d+)?)\s*(万元|万|元)", t)
        if m:
            val = money(m.group(1) + m.group(2))
            if val:
                _set(hard, "deposit", Money(value=val, raw=m.group(0)), cr)
                break
    if r:
        forms = re.findall(r"[①②③④⑤⑥⑦⑧⑨]\s*([^①②③④⑤⑥⑦⑧⑨;；。:：(（]{2,20})", r.content)
        if forms:
            hard.deposit_forms = [f.strip() for f in forms[:8]]

    r = _find_row(rows, "资格审查方式", clause="3.1")
    # 资格条件（4.1/4.2 合并行）
    r = _find_row(rows, "合格投标人资格条件", "资格条件", "资质", clause="4.1")
    if r:
        _set(hard, "qualification", r.content.strip(), r)
        pm = re.search(r"(?:项目负责人|项目经理)[^。]{0,160}?(?:建造师|B证)[^。]{0,80}", r.content)
        if pm:
            _set(hard, "pm_requirement", re.sub(r"\s+", "", pm.group(0)), r)
        jv = _JV_RE.search(r.content)
        if jv:
            n = _JV_N_RE.search(r.content)
            _set(hard, "joint_venture", JVRule(allowed=jv.group(1) is None,
                                               max_members=int(n.group(1)) if n else None, raw=jv.group(0)), r)
        sp = re.search(r"类似工程业绩[”\"]?\s*要求[^\d]{0,6}(\d+)\s*(?:个|项)", r.content) or \
            re.search(r"类似工程业绩[^\d。]{0,30}(\d+)\s*(?:个|项)", r.content)
        if sp:
            _set(hard, "similar_projects_required", int(sp.group(1)), r)
        if "信用" in r.content:
            _set(hard, "credit_score_applied", ("不应用" not in r.content and "不适用" not in r.content), r)

    r = _find_row(rows, "分包", clause="11")
    if r:
        _set(hard, "subcontract_policy", r.content.strip(), r)

    r = _find_row(rows, "投标截止", clause="14.1")
    if r:
        m = re.search(r"\d{4}[-年]\d{1,2}[-月]\d{1,2}[^,，;；]*", to_halfwidth(r.content))
        if m:
            _set(hard, "deadline", m.group(0).strip(), r)

    r = _find_row(rows, "履约担保", clause="29.1")
    if r:
        _set(hard, "performance_bond", r.content.strip(), r)

    r = _find_row(rows, "编制和加密", "加密", clause="20.5")
    if r:
        _set(hard, "ca_own_only", "本单位" in r.content and "数字证书" in r.content, r)

    r = _find_row(rows, "投标文件内容", "要求提交的投标文件", clause="20.1")
    if r:
        _set(hard, "xml_required", "XML" in r.content.upper(), r)

    # 招标文件日期：封面/公告页第一个日期（签署日期下限）
    if doc is not None:
        from services.fujian_check.dates import find_dates

        for p in range(1, min(doc.n_pages, 4) + 1):
            ds = find_dates(doc.page_text(p))
            if ds:
                hard.notice_date = ds[0]
                hard.sources["notice_date"] = Location(doc="tender", page=p, section="第1章 招标公告", clause="招标文件日期", excerpt=ds[0])
                break
    # 招标控制价：前附表通常不含，从第1章招标公告找
    if doc is not None:
        for p in range(1, min(doc.n_pages, 6) + 1):
            txt = to_halfwidth(doc.page_text(p))
            m = re.search(r"(?:招标控制价|最高投标限价|拦标价)[^\d]{0,40}?(\d[\d,]*(?:\.\d+)?)\s*(万元|万|元)", txt)
            if m:
                val = money(m.group(1) + m.group(2))
                if val:
                    hard.control_price = Money(value=val, raw=m.group(0))
                    hard.sources["control_price"] = Location(doc="tender", page=p, section="第1章 招标公告",
                                                             clause="招标控制价", excerpt=m.group(0)[:200])
                    break
    return hard


def project_identity(rows: list[QfbRow]) -> tuple[str, str]:
    """(项目名称, 招标项目编号)。"""
    name = code = ""
    r = _find_row(rows, "项目名称", clause="1.2")
    if r:
        t = to_halfwidth(r.content)
        m = re.search(r"招标项目名称[:：]?\s*(.+?)(?=报建编号|招标项目编号|标段|$)", t)
        if m:
            name = re.sub(r"\s+", "", m.group(1))
        m = re.search(r"招标项目编号[:：]?\s*([A-Z0-9]{8,})", t)
        if m:
            code = m.group(1)
    return name, code
