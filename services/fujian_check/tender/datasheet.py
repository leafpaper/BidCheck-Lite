"""第3章 评标办法和标准数据表 + 3.1.8 施工现场管理人员配备解析。"""
from __future__ import annotations

import re

from services.fujian_check.models import DataSheetRow, Location, ParsedDoc, Section, Sourced, StaffRole, StaffingRequirement
from services.fujian_check.tender._tables import parse_numbered_table, row_location

SECTION_LABEL = "第3章 评标办法和标准数据表"
_DS_TITLE_RE = re.compile(r"评标办法和标准数据表")
_END_RE = re.compile(r"^第\s*2\s*节|^评标办法和标准\s*$|^1\.\s*评标方法|^一、评标方法")

_ROLE_RE = re.compile(
    r"(施工员|质量员|材料员|机械员|安全员|劳务员|资料员|标准员|测量员|试验员|造价员|预算员)\s*[:：]\s*(\d+)\s*人"
)
_PM_RE = re.compile(r"项目负责人\s*(\d)\s*人")
_TL_RE = re.compile(r"项目技术负责人\s*(\d)\s*人")
_LEVEL_RE = re.compile(r"(壹|贰|叁|一|二|三)级(?:及以上)?[^。;；]{0,20}?注册建造师|(?:不低于|具备)\s*(壹|贰|叁|一|二|三)级")
_TITLE_RE = re.compile(r"(中级|高级|初级)(?:及以上)?[^。;；]{0,10}?职称")


def datasheet_pages(doc: ParsedDoc, ch3: Section) -> list[int]:
    """数据表页范围：从标题页起，到第3章第2节（评标办法正文）前。"""
    start = None
    for p in range(ch3.page_start, ch3.page_end + 1):
        if _DS_TITLE_RE.search(doc.page_text(p)) and not doc.pages[p - 1].is_toc:
            start = p
            break
    if start is None:
        return []
    end = ch3.page_end
    for p in range(start + 1, ch3.page_end + 1):
        first_lines = doc.page_text(p).split("\n")[:3]
        if any(_END_RE.search(ln) for ln in first_lines):
            end = p - 1
            break
    return list(range(start, min(end, start + 12) + 1))


def parse_datasheet(doc: ParsedDoc, ch3: Section) -> list[DataSheetRow]:
    pages = datasheet_pages(doc, ch3)
    if not pages:
        return []
    rows = parse_numbered_table(doc, pages)
    # 只保留条款号形如 2.1 / 3.1.8 的行，排除同页范围内其他 4 列表（如危大清单附表）
    rows = [r for r in rows if re.match(r"^\d+(?:\.\d+)*$", r.clause.strip())]
    return [DataSheetRow(no=r.item_no, clause=r.clause, name=r.name, content=r.content,
                         loc=row_location(r, SECTION_LABEL, clause_prefix="数据表第")) for r in rows]


def parse_staffing(text: str, loc: Location | None = None) -> StaffingRequirement:
    req = StaffingRequirement(source=loc)
    m = _PM_RE.search(text)
    if m:
        lvl = _LEVEL_RE.search(text)
        extra = []
        if re.search(r"安全生产考核合格证书?\s*\(?B", text) or "B证" in text:
            extra.append("安B")
        if "本企业在岗" in text:
            extra.append("本企业在岗")
        cert = None
        if lvl:
            cert = (lvl.group(1) or lvl.group(2)) + "级建造师"
        req.roles["项目负责人"] = StaffRole(count=int(m.group(1)), cert=cert, extra=extra)
    m = _TL_RE.search(text)
    if m:
        t = _TITLE_RE.search(text)
        req.roles["项目技术负责人"] = StaffRole(count=int(m.group(1)), cert=(t.group(1) + "职称") if t else None,
                                                extra=["本企业在岗"] if "本企业在岗" in text else [])
    for name, cnt in _ROLE_RE.findall(text):
        cert = None
        if name == "安全员" and re.search(r"C\s*证", text):
            cert = "C证"
        prev = req.roles.get(name)
        # "施工员:2 人" 与 "②施工员2人(其中土建1、设备安装1)" 取最大值
        if prev is None or int(cnt) > prev.count:
            req.roles[name] = StaffRole(count=int(cnt), cert=cert)
    req.one_person_one_post = "一人一职" in text or "不得兼任" in text
    return req


def find_staffing(rows: list[DataSheetRow]) -> StaffingRequirement | None:
    for r in rows:
        if "管理人员" in r.name or r.clause.startswith("3.1.8"):
            return parse_staffing(r.content, r.loc)
    return None


def find_dangerous_works(rows: list[DataSheetRow]) -> list[Sourced]:
    """数据表 4.2 危大工程清单：把"①…②…"或"1、…2、…"拆成标题列表。"""
    for r in rows:
        if "危大" in r.name or "危险性较大" in r.name or r.clause.startswith("4.2"):
            text = r.content
            # 优先按 ①②③ 圆圈数字切；其次按"危大工程清单N:"；再次按"N、"（顿号，避免小数点）
            if re.search(r"[①②③④⑤⑥⑦⑧⑨⑩]", text):
                parts = re.split(r"[①②③④⑤⑥⑦⑧⑨⑩]", text)
            elif re.search(r"危大工程清单\s*\d", text):
                parts = re.split(r"危大工程清单\s*\d+\s*[:：]", text)
            else:
                parts = re.split(r"(?<![\d.])\d{1,2}、", text)
            items = []
            for p in parts:
                p = re.sub(r"\s+", "", p).strip(" ;；。,，")
                if len(p) > 4 and not re.match(r"^(本项目)?危大工程清单[:：]?$", p):
                    items.append(p)
            return [Sourced(value=it, loc=r.loc) for it in items]
    return []


def find_similar_project_years(rows: list[DataSheetRow]) -> int | None:
    """3.1.9「自…发布招标公告之日的前 5 年内」→ 5。"""
    for r in rows:
        if "类似工程业绩" in r.name or r.clause.startswith("3.1.9"):
            m = re.search(r"前\s*([一二三四五六七八九十\d]{1,2})\s*年", r.content)
            if m:
                from services.fujian_check.textnorm import cn_to_int

                v = m.group(1)
                return int(v) if v.isdigit() else cn_to_int(v)
    return None


_SOCIAL_START_RE = re.compile(r"上\s*([一二三四五六七八九十\d]{1,2})\s*个月\s*为始点")
_SOCIAL_MONTHS_RE = re.compile(r"(?:累计|连续缴费累计|连续)\s*([一二三四五六七八九十\d]{1,2})\s*个月")


def find_social_window(text: str) -> tuple[int | None, int | None]:
    """社保要求「截止之日的上二个月为始点并往前追溯连续缴费累计六个月」→ (2, 6)。"""
    from services.fujian_check.textnorm import cn_to_int

    def _n(s: str) -> int | None:
        return int(s) if s.isdigit() else cn_to_int(s)
    a = _SOCIAL_START_RE.search(text or "")
    b = _SOCIAL_MONTHS_RE.search(text or "")
    return (_n(a.group(1)) if a else None), (_n(b.group(1)) if b else None)


def find_similar_projects(rows: list[DataSheetRow]) -> int | None:
    for r in rows:
        if "类似工程业绩" in r.name or r.clause.startswith("3.1.9"):
            m = re.search(r"要求[^\d。]{0,8}(\d+)\s*(?:个|项)", r.content)
            if m:
                return int(m.group(1))
            if "/" in r.content[:10] or "不要求" in r.content or "无" in r.content[:5]:
                return 0
    return None
