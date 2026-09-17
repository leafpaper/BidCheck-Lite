"""政府采购竞争性磋商文件（工程类）解析 → TenderRequirements。

复用统一模型：
- qianfubiao   ← 前附表表1（项号|条款号|编列内容，3 列，嵌套资格材料表）
- forms        ← 资格材料清单（section=资格文件，no=序号）+ 第五章 附件1-8 及子附件（section=响应文件）
- rejection    ← 须知 14.2.1 情形1-7 / 14.2.2 其他情形 / 前附表第12项无效标条款 / 第三章散落"无效响应" / ★技术 1-4 / ★商务 1-8
- datasheet    ← 第三章【项号N】1-12（非★技术指标，每项 5 分）
- appendix_params ← 评分项（"评分:xxx" → 分值/客观）、份数、预算等
- hard         ← 最高限价、预算、保证金、有效期、工期、质量、资质、项目负责人、联合体、分包、中小企业
"""
from __future__ import annotations

import re

from services.fujian_check.models import (
    DataSheetRow,
    HardValues,
    JVRule,
    Location,
    Money,
    ParsedDoc,
    QfbRow,
    RejectionClause,
    RequiredForm,
    Section,
    Sourced,
    StaffRole,
    StaffingRequirement,
    TenderProfile,
    TenderRequirements,
)
from services.fujian_check.tender._tables import tables_for_pages
from services.fujian_check.tender.sections import find_section, locate_sections
from services.fujian_check.textnorm import money, to_halfwidth

SEC_QFB = "第二章第1节 竞争性磋商须知前附表"
SEC_NOTICE = "第二章第2节 竞争性磋商须知"
SEC_EVAL = "第二章 专项附件 评审的标准和方法"
SEC_REQ = "第三章 采购内容及要求"
SEC_FORMS = "第五章 首次响应文件格式"

_NUM_SPACED = re.compile(r"^\s*\d(?:\s*\d)*\s*\.?\s*$")


def _n(s: str) -> str:
    return re.sub(r"\s+", "", to_halfwidth(s or ""))


def _loc(p: int, section: str, clause: str | None = None, excerpt: str = "") -> Location:
    return Location(doc="tender", page=p, section=section, clause=clause, excerpt=excerpt[:300])


# ---------------- 前附表表1 ----------------

def parse_qfb(doc: ParsedDoc, pages: list[int]) -> tuple[list[QfbRow], list[RequiredForm], list[tuple[str, str, int]]]:
    """返回 (前附表行, 资格材料清单, 特定资格条件[(概况, 描述, 页)])。"""
    tables = tables_for_pages(doc, pages)
    rows: list[QfbRow] = []
    quals: list[RequiredForm] = []
    special: list[tuple[str, str, int]] = []
    prev_kind = ""
    for p in pages:
        for tb in tables.get(p, []):
            header = _n("".join(tb[0])) if tb else ""
            if "资格审查要求概况" in header and "序号" in header:
                kind = "qual"
            elif "资格审查要求概况" in header:
                kind = "special"
            elif tb and len(tb[0]) == 2 and "special" in prev_kind:
                kind = "special"          # 特定资格条件表跨页续表（p8，无表头，2 列）
            elif "编列内容" in header or (tb and len(tb[0]) == 3):
                kind = "qfb"
            else:
                kind = "other"
            prev_kind = kind if kind == "special" or "special" not in prev_kind else prev_kind + "+" + kind
            for raw in tb:
                cells = [c.strip() for c in raw]
                if not any(cells):
                    continue
                joined = _n("".join(cells))
                if joined.startswith(("项号", "序号", "资格审查要求概况", "编列内容")):
                    continue
                if kind == "qual" and len(cells) >= 3 and _NUM_SPACED.match(cells[0] or ""):
                    no = _n(cells[0]).rstrip(".")
                    quals.append(RequiredForm(section="资格文件", no=no, title=_n(cells[1]),
                                              optional="若有" in cells[1] or "如有" in cells[1],
                                              loc=_loc(p, SEC_QFB, f"前附表第1项-资格材料{no}", cells[2])))
                elif kind == "special" and len(cells) == 2 and cells[0]:
                    special.append((_n(cells[0]), cells[1], p))
                elif kind in ("qfb", "other") and len(cells) >= 3 and _NUM_SPACED.match(cells[0] or "")                         and cells[1] and not re.match(r"^[\d\.\s]+$", cells[1]) and len(_n(cells[1])) <= 40:
                    # 无表头的资格材料续表（p6/p7）：序号 | 材料名称 | 评审点
                    no = _n(cells[0]).rstrip(".")
                    title = _n(cells[1])
                    if title.startswith("联合体协议"):
                        no = "11"
                    quals.append(RequiredForm(section="资格文件", no=no, title=title, optional="若有" in title or "如有" in title,
                                              loc=_loc(p, SEC_QFB, f"前附表第1项-资格材料{no}", cells[2])))
                elif kind in ("qfb", "other") and len(cells) >= 3:
                    if _NUM_SPACED.match(cells[0] or "") and not _n(cells[1]).startswith("3.2.1") or (
                            _NUM_SPACED.match(cells[0] or "") and len(cells) == 3 and "条款" not in header and kind == "qfb"):
                        item = int(_n(cells[0]).rstrip("."))
                        clause = _n(cells[1])
                        content = cells[2]
                        # 嵌套的资格材料表会被 pdfplumber 当作同表行（如 p7 "1 | 联合体协议"），按内容区分
                        if item <= 13 and (clause == "" or re.match(r"^\d+(\.\d+)*$", clause)) and not re.match(r"^(联合体协议|中小企业声明函|信用记录)", _n(content)):
                            rows.append(QfbRow(item_no=item, clause=clause, name="", content=content,
                                               loc=_loc(p, SEC_QFB, f"前附表第{item}项", content)))
                        elif kind == "qfb" and _n(content).startswith(("联合体协议", "中小企业声明函", "信用记录")):
                            quals.append(RequiredForm(section="资格文件", no=str(item if item != 1 else 11), title=_n(content).split("①")[0][:30],
                                                      optional="若有" in content, loc=_loc(p, SEC_QFB, "前附表第1项-资格材料", content)))
                    elif rows and not cells[0] and not cells[1] and cells[2]:
                        rows[-1].content = (rows[-1].content + " " + cells[2]).strip()
                        rows[-1].loc.page_end = p
    # 第1项（资格要求）本身在 p5 第一张表里没有项号，手工补
    if not any(r.item_no == 1 for r in rows):
        rows.insert(0, QfbRow(item_no=1, clause="3.2.1", name="供应商的资格要求", content="见第一章及资格材料清单",
                              loc=_loc(pages[0], SEC_QFB, "前附表第1项")))
    rows.sort(key=lambda r: r.item_no)
    # 去重
    seen: set[str] = set()
    uq: list[RequiredForm] = []
    for q in quals:
        k = q.no + q.title[:8]
        if k not in seen:
            seen.add(k)
            uq.append(q)
    uq.sort(key=lambda f: int(f.no) if f.no.isdigit() else 99)
    return rows, uq, special


# ---------------- 无效情形 / ★ ----------------

_STAR_TECH_RE = re.compile(r"^★\s*[（(]实质性要求\s*(\d+)[)）]\s*(.*)$")
_ITEM_RE = re.compile(r"^【项号\s*(\d+)】\s*(.*)$")
_INVALID_RE = re.compile(r"无效响应|响应无效|按废标处理|视为无效报价|无效报价|不符合磋商文件中规定的其它实质性条款|资格审查不合格")


def parse_ch3(doc: ParsedDoc, sec: Section) -> tuple[list[RejectionClause], list[DataSheetRow], list[RejectionClause], StaffingRequirement | None, list[str]]:
    """第三章：★技术实质性要求、【项号】、商务★表、散落无效条款、人员要求。"""
    stars: list[RejectionClause] = []
    items: list[DataSheetRow] = []
    scattered: list[RejectionClause] = []
    cur: RejectionClause | DataSheetRow | None = None
    for p, _o, line in doc.iter_lines(range(sec.page_start, sec.page_end + 1)):
        ln = to_halfwidth(line).strip()
        m = _STAR_TECH_RE.match(ln)
        if m:
            cur = RejectionClause(id=f"★技术{m.group(1)}", group="技术文件", text=m.group(2).strip(),
                                  loc=_loc(p, SEC_REQ, f"★实质性要求{m.group(1)}"))
            stars.append(cur)
            continue
        m = _ITEM_RE.match(ln)
        if m:
            cur = DataSheetRow(no=int(m.group(1)), clause=f"项号{m.group(1)}", name=m.group(2)[:40], content=m.group(2),
                               loc=_loc(p, SEC_REQ, f"【项号{m.group(1)}】"))
            items.append(cur)
            continue
        if re.match(r"^(三、商务条件|四、其他事项|注[:：]|★注)", ln):
            cur = None
        if cur is not None and len(getattr(cur, "text", getattr(cur, "content", ""))) < 900:
            if isinstance(cur, RejectionClause):
                cur.text = (cur.text + ln).strip()
                cur.loc.page_end = p
            else:
                cur.content = (cur.content + ln).strip()
                cur.loc.page_end = p
        if _INVALID_RE.search(ln) and not ln.startswith("★"):
            scattered.append(RejectionClause(id=f"第三章p{p}", group="须知正文", text=ln[:200],
                                             loc=_loc(p, SEC_REQ, None, ln)))
    for c in stars:
        c.loc.excerpt = c.text[:300]
    # 商务条件 ★ 表
    biz: list[RejectionClause] = []
    tables = tables_for_pages(doc, list(range(sec.page_start, sec.page_end + 1)))
    for p in range(sec.page_start, sec.page_end + 1):
        for tb in tables.get(p, []):
            for raw in tb:
                cells = [c.strip() for c in raw] + [""] * (4 - len(raw))
                if _n(cells[0]).isdigit() and "★" in cells[1] and cells[2]:
                    biz.append(RejectionClause(id=f"★商务{_n(cells[0])}", group="商务初审", text=f"{_n(cells[2])}：{cells[3]}",
                                               loc=_loc(p, SEC_REQ, f"三、商务条件 序号{_n(cells[0])}", cells[3])))
    # 人员（【项号11】）
    staffing = None
    it11 = next((i for i in items if re.search(r"团队人员|人员要求|人员配置", i.name)), None) or \
        next((i for i in items if re.search(r"项目负责人.{0,12}\d\s*人", i.content)), None)
    if it11:
        staffing = StaffingRequirement(source=it11.loc)
        t = to_halfwidth(it11.content).replace(" ", "")
        for role in ("项目负责人", "项目技术负责人", "施工员", "质量员", "材料员", "机械员", "安全员", "劳务员", "资料员"):
            cnt = sum(int(x) for x in re.findall(role + r"(?:\(即项目经理\)|\(质检员\)|\(安全员\)|专职安全生产管理人员)?(?:\(.{0,8}\))?(\d)人", t))
            if role == "安全员":
                cnt = cnt or sum(int(x) for x in re.findall(r"专职安全生产管理人员\(安全员\)(\d)人", t))
            if cnt:
                # 证书要求从该岗位后 120 字里读，读不到才用福建模板常见默认值
                seg_m = re.search(role + r".{0,120}", t)
                seg = seg_m.group(0) if seg_m else ""
                cert = None
                extra: list[str] = []
                if role == "项目负责人":
                    lv = re.search(r"([一二壹贰]级)(?:及以上)?[^。]{0,12}建造师", seg)
                    cert = (lv.group(1).replace("壹", "一").replace("贰", "二") + "建造师") if lv else "二级建造师"
                    extra = [x for x, k in (("安B", r"安全生产考核|B类|B证"), ("本单位在岗", r"本单位|本企业")) if re.search(k, seg)] or ["安B", "本单位在岗"]
                elif role == "项目技术负责人":
                    tl = re.search(r"(中级|高级|初级)(?:及以上)?[^。]{0,8}职称", seg)
                    cert = (tl.group(1) + "职称") if tl else "工程系列职称"
                elif role == "安全员":
                    cert = "C证"
                else:
                    cert = "岗位证书" if re.search(r"证书|培训合格", seg) else None
                staffing.roles[role] = StaffRole(count=cnt, cert=cert, extra=extra)
        staffing.one_person_one_post = "不得相互兼任" in t or "不可兼岗" in t or "不得重复使用" in t
    return stars, items, biz + scattered, staffing, []


_DECL_TRIGGER_RE = re.compile(
    r"(未提供|不提供|未出具|未按要求提供|未作出|未承诺|未在规定|未按规定)[^。；;]{0,40}?"
    r"(按废标处理|无效响应|响应无效|视为无效|不符合[^。；;]{0,15}实质性|资格审查不合格|否决|则无效|作无效|无效处理)"
)
_DECL_NOUN_RE = re.compile(r"([一-龥]{2,10}?)(专项声明|声明函|保证函|承诺函|承诺书)")


def parse_declarations(doc: ParsedDoc, pages: list[int], section: str) -> list[RejectionClause]:
    """招标文件里「须出具 XX 专项声明/保证函，未提供按废标/无效处理」类硬要求，按主题去重。

    主题词来自 bid/gov_cs.DECL_TOPICS（团意险、代理服务费、分包、缺陷责任期…），找不到主题词时取「XX声明函」里的 XX。
    """
    from services.fujian_check.bid.gov_cs import DECL_TOPICS

    out: list[RejectionClause] = []
    seen: set[str] = set()
    for p in pages:
        t = _n(doc.page_text(p))
        for m in _DECL_TRIGGER_RE.finditer(t):
            win = t[max(0, m.start() - 300): m.end()]
            topic = None
            pos = -1
            for k in DECL_TOPICS:
                i = win.rfind(k)
                if i > pos:
                    pos, topic = i, k
            key = DECL_TOPICS.get(topic) if topic else None
            if key is None:
                nm = list(_DECL_NOUN_RE.finditer(win))
                if nm:
                    key = nm[-1].group(1).lstrip("出具作出提供") + nm[-1].group(2)
            if not key or key in seen:
                continue
            seen.add(key)
            # 要求原文：从主题词所在句开始到触发句结束
            start = 0
            if topic:
                tp = win.rfind(topic)
                start = max(win.rfind("。", 0, tp) + 1, win.rfind("；", 0, tp) + 1, 0)
            else:
                start = max(win.rfind("。", 0, len(win) - (m.end() - m.start()) - 1) + 1, 0)
            text = win[start:].strip("。；;() （）")
            out.append(RejectionClause(id=f"专项声明-{key}", group="商务初审", text=text[:260], loc=_loc(p, section, "专项声明/保证函", text)))
    return out


def parse_invalid_cases(doc: ParsedDoc, notice: Section) -> list[RejectionClause]:
    out: list[RejectionClause] = []
    tables = tables_for_pages(doc, list(range(notice.page_start, min(notice.page_end, notice.page_start + 8) + 1)))
    for p in sorted(tables):
        for tb in tables[p]:
            for raw in tb:
                cells = [c.strip() for c in raw]
                if len(cells) >= 3 and _n(cells[0]).isdigit() and _n(cells[1]).startswith("情形"):
                    out.append(RejectionClause(id=f"14.2.1-{_n(cells[0])}", group="商务初审", text=cells[2],
                                               loc=_loc(p, SEC_NOTICE, f"14.2.1 {_n(cells[1])}", cells[2])))
                elif len(cells) == 2 and _n(cells[0]) == "其他情形" and cells[1]:
                    out.append(RejectionClause(id=f"14.2.2-{len(out) + 1}", group="其他", text=cells[1],
                                               loc=_loc(p, SEC_NOTICE, "14.2.2", cells[1])))
    return out


def parse_scoring(doc: ParsedDoc, pages: list[int]) -> dict[str, Sourced]:
    out: dict[str, Sourced] = {}
    text = "\n".join(doc.page_text(p) for p in pages)
    for m in re.finditer(r"(报价|技术|商务)部分评分\s*P[FTB]\s*满分为\s*([\d.]+)分", to_halfwidth(text)):
        out[f"评分:{m.group(1)}部分满分"] = Sourced(value=m.group(2), loc=_loc(pages[0], SEC_EVAL, "综合评分法"))
    tables = tables_for_pages(doc, pages)
    for p in pages:
        for tb in tables.get(p, []):
            for raw in tb:
                cells = [c.strip() for c in raw]
                if len(cells) >= 4 and re.match(r"^\d+\.", _n(cells[0])) and re.match(r"^[\d.]+$", _n(cells[1])):
                    out[f"评分:{_n(cells[0])}"] = Sourced(value=f"{_n(cells[1])}分|客观:{_n(cells[2])}|{cells[3][:160]}",
                                                        loc=_loc(p, SEC_EVAL, _n(cells[0]), cells[3]))
    return out


def parse_forms_ch5(doc: ParsedDoc, sec: Section) -> list[RequiredForm]:
    """第五章目录 附件1-8 + 正文子附件标题 附件N-M。"""
    forms: dict[str, RequiredForm] = {}
    toc_re = re.compile(r"^附件\s*(\d+)\s*[:：]\s*(.+?)\s*$")
    sub_re = re.compile(r"^附件\s*(\d+(?:-\d+){1,2})\s*(.+?)\s*$")
    for p, _o, line in doc.iter_lines(range(sec.page_start, sec.page_end + 1)):
        ln = to_halfwidth(line).strip()
        m = toc_re.match(ln)
        if m and m.group(1) not in forms:
            title = m.group(2)
            forms[m.group(1)] = RequiredForm(section="响应文件", no=m.group(1), title=re.sub(r"[（(]若有[)）]", "", title).strip(),
                                            optional="若有" in title, loc=_loc(p, SEC_FORMS, f"附件{m.group(1)}"))
            continue
        m = sub_re.match(ln)
        if m and m.group(1) not in forms and len(m.group(2)) <= 40:
            title = m.group(2)
            forms[m.group(1)] = RequiredForm(section="响应文件", no=m.group(1), title=re.sub(r"[（(]若有[)）]", "", title).strip(),
                                            optional="若有" in title, loc=_loc(p, SEC_FORMS, f"附件{m.group(1)}"))
    def key(no: str):
        return [int(x) for x in no.split("-")]
    return sorted(forms.values(), key=lambda f: key(f.no))


# ---------------- 硬值 ----------------

def extract_hard(doc: ParsedDoc, rows: list[QfbRow], special: list[tuple[str, str, int]], biz: list[RejectionClause],
                 ch3: Section | None) -> tuple[HardValues, str, str]:
    h = HardValues()
    head_pages = list(range(1, min(8, doc.n_pages) + 1))
    head = "\n".join(doc.page_text(p) for p in head_pages)
    ht = to_halfwidth(head)

    def _page_of(pattern: str) -> int:
        for p in head_pages:
            if re.search(pattern, to_halfwidth(doc.page_text(p))):
                return p
        return head_pages[0]
    m = re.search(r"最高限价\s*[（(]?(?:元|万元)?[)）]?\s*[:：]\s*([\d,\.]+)\s*(万元|元)?", ht) or \
        re.search(r"(?:预算金额|采购预算)\s*[（(]?(?:元|万元)?[)）]?\s*[:：]\s*([\d,\.]+)\s*(万元|元)?", ht)
    if m:
        unit = m.group(2) or ("万元" if "万元" in m.group(0) else "元")
        h.control_price = Money(value=money(m.group(1) + unit) or 0, raw=m.group(0))
        h.sources["control_price"] = _loc(_page_of(r"最高限价|预算金额|采购预算"), "第一章 采购邀请书", "采购包最高限价", m.group(0))
    m = re.search(r"保证金金额\s*[（(]?(?:元)?[)）]?\s*[:：]\s*([\d,\.]+)", ht)
    if m:
        h.deposit = Money(value=money(m.group(1) + "元") or 0, raw=m.group(0))
        h.sources["deposit"] = _loc(_page_of(r"保证金金额"), "第一章 采购邀请书", "采购包保证金金额", m.group(0))
    name = code = ""
    m = re.search(r"项目名称\s*[:：]\s*(\S{4,60})", ht)
    if m:
        name = m.group(1)
    m = re.search(r"项目编号\s*[:：]\s*(\[?[\w\[\]]{8,40})", ht)
    if m:
        code = m.group(1)
    for r in rows:
        c = to_halfwidth(r.content)
        if r.item_no == 4 or "响应文件有效期" in c:
            mm = re.search(r"(\d{2,3})\s*个?日历日", c)
            if mm:
                h.validity_days = int(mm.group(1))
                h.sources["validity_days"] = r.loc
        if r.item_no == 2 or "联合体" in c[:30]:
            h.joint_venture = JVRule(allowed="不接受" not in c, raw=c[:60])
            h.sources["joint_venture"] = r.loc
    for title, desc, p in special:
        d = to_halfwidth(desc)
        if "资质" in d and "承包" in d:
            h.qualification = d.split("。")[0]
            h.sources["qualification"] = _loc(p, SEC_QFB, "特定资格条件", d)
        if "项目负责人" in d and "建造师" in d:
            h.pm_requirement = re.sub(r"\s+", "", re.split(r"注[:：]", d)[0])[:300]
            h.sources["pm_requirement"] = _loc(p, SEC_QFB, "特定资格条件", d)
        if "中小企业" in title or "中小企业声明函" in d:
            h.credit_score_applied = None
    for b in biz:
        if b.id == "★商务1" or "交货时间" in b.text[:10]:
            mm = re.search(r"(\d{2,4})\s*(?:个)?(?:日历)?[日天]", b.text)
            if mm:
                h.duration_days = int(mm.group(1))
                h.sources["duration_days"] = b.loc
        if "交货条件" in b.text[:10] or "质量" in b.text[:20]:
            h.quality = b.text.split("：", 1)[-1][:120]
            h.sources["quality"] = b.loc
        if "履约保证金" in b.text[:10]:
            h.performance_bond = b.text.split("：", 1)[-1][:200]
            h.sources["performance_bond"] = b.loc
    if ch3 and h.duration_days is None:
        t3 = to_halfwidth(doc.page_text(ch3.page_start))
        mm = re.search(r"工期\s*(\d{2,4})\s*天", t3)
        if mm:
            h.duration_days = int(mm.group(1))
            h.sources["duration_days"] = _loc(ch3.page_start, SEC_REQ, "一、项目概况")
    if ch3:
        t3 = "\n".join(doc.page_text(p) for p in range(ch3.page_start, ch3.page_end + 1))
        if re.search(r"不允许分包或转包|不允许.{0,20}转包、分包", t3):
            h.subcontract_policy = "不允许分包或转包"
    return h, name, code


def extract_gov_cs(doc: ParsedDoc, profile: TenderProfile) -> TenderRequirements:
    req = TenderRequirements(profile=profile)
    req.sections = locate_sections(doc)
    warn = req.parse_warnings
    ch2 = find_section(req.sections, 2)
    ch3 = find_section(req.sections, 3)
    ch5 = find_section(req.sections, 5)
    if not ch2:
        warn.append("未找到第二章")
        return req
    # 前附表页：第2章起到"专项附件"或"第2节"前
    qfb_pages: list[int] = []
    eval_pages: list[int] = []
    notice_start = None
    for p in range(ch2.page_start, ch2.page_end + 1):
        t = doc.page_text(p)
        if notice_start is None and re.search(r"^第\s*2\s*节", t, re.M):
            notice_start = p
        if "评审的标准和方法" in t and re.search(r"专项附件[:：]?\s*评审的标准和方法", t):
            eval_pages.append(p)
        elif eval_pages and notice_start is None:
            eval_pages.append(p)
        elif notice_start is None:
            qfb_pages.append(p)
    notice = Section(chapter=2, section=2, title="竞争性磋商须知", page_start=notice_start or ch2.page_end, page_end=ch2.page_end)
    req.qianfubiao, quals, special = parse_qfb(doc, qfb_pages)
    if len(req.qianfubiao) < 8:
        warn.append(f"前附表仅解析出 {len(req.qianfubiao)} 项")
    req.forms += quals
    for title, desc, p in special:
        req.forms.append(RequiredForm(section="资格文件", no=f"特定-{title[:12]}", title=title, optional=False,
                                      loc=_loc(p, SEC_QFB, "特定资格条件", desc)))
    req.rejection += parse_invalid_cases(doc, notice)
    _seen_ids: set[str] = set()
    # 前附表第12项无效标条款 a-d
    r12 = next((r for r in req.qianfubiao if r.item_no == 12), None)
    if r12:
        for m in re.finditer(r"([a-d])\.([^；;。]{6,80})", to_halfwidth(r12.content)):
            req.rejection.append(RejectionClause(id=f"前附表12-{m.group(1)}", group="其他", text=m.group(2), loc=r12.loc))
        if "代理服务费" in r12.content and "保证函" in r12.content:
            req.rejection.append(RejectionClause(id="前附表12-代理服务费保证函", group="商务初审",
                                                 text="供应商应出具代理服务费在成交后即时缴纳的专项保证函，未提供将被视为不符合磋商文件中规定的其它实质性条款", loc=r12.loc))
    if eval_pages:
        req.appendix_params.update(parse_scoring(doc, eval_pages))
    # 「须出具 XX 专项声明/保证函，未提供按废标」类要求：前附表 + 须知正文 + 第三章
    decl_pages = qfb_pages + list(range(notice.page_start, notice.page_end + 1)) + (list(range(ch3.page_start, ch3.page_end + 1)) if ch3 else [])
    req.rejection += parse_declarations(doc, decl_pages, SEC_REQ)
    biz: list[RejectionClause] = []
    if ch3:
        stars, items, biz_and_scattered, staffing, _ = parse_ch3(doc, ch3)
        biz = [c for c in biz_and_scattered if c.id.startswith("★商务")]
        req.rejection += stars + biz_and_scattered
        req.datasheet = items
        req.staffing = staffing
        req.dangerous_works = []
    else:
        warn.append("未找到第三章")
    dedup: list[RejectionClause] = []
    for c in req.rejection:
        if c.id not in _seen_ids:
            _seen_ids.add(c.id)
            dedup.append(c)
    req.rejection = dedup
    if ch5:
        req.forms += parse_forms_ch5(doc, ch5)
    else:
        warn.append("未找到第五章")
    req.hard, req.project_name, req.project_code = extract_hard(doc, req.qianfubiao, special, biz, ch3)
    r6 = next((r for r in req.qianfubiao if r.item_no == 6), None)
    if r6:
        req.appendix_params["响应文件份数"] = Sourced(value=re.sub(r"\s+", " ", r6.content)[:200], loc=r6.loc)
    return req
