"""投标文件索引：节边界、表单页范围、扫描页归属、已标价清单范围、技术文件分段。

投标文件（福州平台生成）完全镜像招标文件第 8 章：第1节 资格文件 → 第2节 商务文件 → 技术文件（暗标，无节标题）→ 定标文件。
"""
from __future__ import annotations

import re

from services.fujian_check.models import (
    BidIndex,
    FormRange,
    Location,
    ParsedDoc,
    RequiredForm,
    TechSegment,
    TenderRequirements,
)
from services.fujian_check.profiles.base import Profile
from services.fujian_check.textnorm import similarity, to_halfwidth

_FORM_TITLE_RE = re.compile(r"^([一二三四五六七八九十]{1,3})、\s*(\S.*?)\s*[.…·]*\s*(?:\(\s*\d*\s*\)|（\s*\d*\s*）)?$")
_OPTIONAL_RE = re.compile(r"[（(]\s*(如有时|如有|若有)\s*[)）]")
_DANGER_RE = re.compile(r"^危大工程清单\s*(\d+)\s*[:：]\s*(.+)$")
_BOQ_HEADER_RE = re.compile(r"第\s*\d+\s*页\s*共\s*\d+\s*页")
_COVER_BIDDER_RE = re.compile(r"投\s*标\s*人\s*[:：]\s*(.+?)\s*[（(]\s*盖单位公章")
_COVER_LEGAL_RE = re.compile(r"法定代表人(?:或其委托代理人)?\s*[:：]\s*(.{2,6}?)\s*[（(]\s*盖章")
FUZZY_THRESHOLD = 0.75
TOC_TITLE_LINES = 4


def _clean_title(t: str) -> str:
    t = _OPTIONAL_RE.sub("", to_halfwidth(t))
    t = re.sub(r"[…\.·]{2,}.*$", "", t)
    t = re.sub(r"[“”\"'‘’]", "", t)
    return re.sub(r"\s+", "", t).strip(" :：")


def _page_head_lines(doc: ParsedDoc, p: int, n: int = 4) -> list[str]:
    return [ln.strip() for ln in doc.page_text(p).split("\n") if ln.strip()][:n]


def _is_form_toc(doc: ParsedDoc, p: int) -> bool:
    """目录页：多数行都是"X、标题"且无正文句子（承诺函正文里的"一、二、"编号不算）。"""
    lines = [ln.strip() for ln in doc.page_text(p).split("\n") if ln.strip()]
    if not lines:
        return False
    hits = sum(1 for ln in lines if _FORM_TITLE_RE.match(to_halfwidth(ln)))
    has_toc_mark = "目录" in "".join(lines[:2]).replace(" ", "") or any("…" in ln for ln in lines)
    return hits >= TOC_TITLE_LINES and (hits / len(lines) >= 0.5 or has_toc_mark)


def detect_sections(doc: ParsedDoc, profile: Profile) -> dict[str, tuple[int, int]]:
    starts: dict[str, int] = {}
    for p in range(1, doc.n_pages + 1):
        head = " \n".join(_page_head_lines(doc, p, 3))
        for pat, name in profile.bid_section_patterns.items():
            if name in starts:
                continue
            if re.search(pat, to_halfwidth(head), re.M):
                starts[name] = p
    # 技术文件：无节标题 → 第一个 "危大工程清单1" 页往前回溯到非扫描页之后
    if "技术文件" not in starts:
        for p in range(starts.get("商务文件", 1), doc.n_pages + 1):
            first = _page_head_lines(doc, p, 1)
            if first and _DANGER_RE.match(to_halfwidth(first[0])):
                q = p
                while q - 1 > starts.get("商务文件", 1) and doc.pages[q - 2].is_scanned:
                    q -= 1
                starts["技术文件"] = q
                break
    ordered = sorted(starts.items(), key=lambda kv: kv[1])
    out: dict[str, tuple[int, int]] = {}
    for i, (name, s) in enumerate(ordered):
        e = ordered[i + 1][1] - 1 if i + 1 < len(ordered) else doc.n_pages
        out[name] = (s, max(s, e))
    return out


def _match_required(title: str, candidates: list[RequiredForm], used: set[int],
                    no_cn: str | None = None) -> tuple[RequiredForm | None, str]:
    ct = _clean_title(title)
    # 同名表单（定标文件里有两个"承诺函"）：编号一致者优先
    if no_cn:
        for i, f in enumerate(candidates):
            if i in used or f.no != no_cn:
                continue
            ft = _clean_title(f.title)
            if ct == ft or ct.replace("的", "") == ft.replace("的", "") or similarity(ct, ft) >= FUZZY_THRESHOLD:
                return f, "exact"
    for i, f in enumerate(candidates):
        if i in used:
            continue
        ft = _clean_title(f.title)
        if ct == ft or ct.replace("的", "") == ft.replace("的", ""):
            return f, "exact"
    for i, f in enumerate(candidates):
        if i in used:
            continue
        for al in f.aliases:
            a = _clean_title(al)
            if a and (a == ct or a in ct or ct in a):
                return f, "alias"
    best, best_s = None, 0.0
    for i, f in enumerate(candidates):
        if i in used:
            continue
        s = similarity(ct, f.title)
        if s > best_s:
            best, best_s = f, s
    if best is not None and best_s >= FUZZY_THRESHOLD:
        return best, "fuzzy"
    return None, "missing"


def locate_forms(doc: ParsedDoc, sections: dict[str, tuple[int, int]], req: TenderRequirements) -> list[FormRange]:
    out: list[FormRange] = []
    for sec_name, (s, e) in sections.items():
        if sec_name == "技术文件":
            continue
        required = req.forms_in(sec_name)  # type: ignore[arg-type]
        used: set[int] = set()
        found: list[tuple[int, str, RequiredForm, str]] = []
        for p in range(s, e + 1):
            if _is_form_toc(doc, p):
                continue
            head = _page_head_lines(doc, p, 3)
            # 标题折行：第 1 行以"X、"开头且第 2 行不是新标题/表头时拼接
            cands: list[tuple[str | None, str]] = []
            for k, ln in enumerate(head[:2]):
                lnh = to_halfwidth(ln)
                m = _FORM_TITLE_RE.match(lnh)
                if m:
                    title = m.group(2)
                    if k + 1 < len(head) and len(title) < 12:
                        nxt = to_halfwidth(head[k + 1])
                        if not _FORM_TITLE_RE.match(nxt) and not re.search(r"[:：]|^\d|^[(（]", nxt) and len(nxt) <= 30:
                            cands.append((m.group(1), title + nxt))
                    cands.append((m.group(1), title))
                else:
                    for f in required:
                        if any(_clean_title(al) and _clean_title(al) in _clean_title(lnh) for al in f.aliases):
                            cands.append((None, lnh))
                            break
            for no_cn, title in cands:
                ct = _clean_title(title)
                if len(ct) < 2 or len(ct) > 40 or re.search(r"[,，。;；)]", ct):
                    continue
                f, kind = _match_required(title, required, used, no_cn)
                if f is None:
                    continue
                used.add(required.index(f))
                found.append((p, ct, f, kind))
                break
        found.sort(key=lambda x: x[0])
        for i, (p, title, f, kind) in enumerate(found):
            end = found[i + 1][0] - 1 if i + 1 < len(found) else e
            end = max(p, end)
            scanned = [q for q in range(p, end + 1) if doc.pages[q - 1].is_scanned]
            out.append(FormRange(form=f, matched_title=title, page_start=p, page_end=end,
                                 scanned_pages=scanned, match_kind=kind))
        # 已标价工程量清单：无表单标题页，按页眉"第x页 共y页"范围识别
        boq = boq_range(doc, {sec_name: (s, e)}) if sec_name == "商务文件" else None
        # 缺失表单：若按第8章顺序落在相邻已匹配表单之间且该区间有扫描页 → 疑似扫描件提交
        for i, f in enumerate(required):
            if i in used:
                continue
            if boq and "工程量清单" in f.title and "编制人员" not in f.title:
                out.append(FormRange(form=f, matched_title="已标价工程量清单(按页眉识别)", page_start=boq[0],
                                     page_end=boq[1], scanned_pages=[], match_kind="alias"))
                used.add(i)
                continue
            prev = next((fr for _p, _t, ff, _k in reversed(found) if (ff.no and required.index(ff) < i)
                         for fr in [ff]), None)
            prev_range = next((x for x in found if x[2] is prev), None) if prev else None
            if prev_range:
                p0 = prev_range[0]
                p1 = next((x[0] - 1 for x in found if x[0] > p0), e)
                scanned = [q for q in range(p0 + 1, p1 + 1) if doc.pages[q - 1].is_scanned]
                if scanned:
                    out.append(FormRange(form=f, matched_title="", page_start=min(scanned), page_end=max(scanned),
                                         scanned_pages=scanned, match_kind="scanned"))
                    continue
            out.append(FormRange(form=f, matched_title="", match_kind="missing"))
        # 已标价清单不属于任何表单标题页：把跨过清单起点的表单范围裁到清单之前
        if boq:
            for fr in out:
                if fr.match_kind in ("exact", "alias", "fuzzy", "scanned") and fr.page_start and fr.page_end \
                        and fr.page_start < boq[0] <= fr.page_end and "工程量清单" not in fr.matched_title:
                    fr.page_end = boq[0] - 1
                    fr.scanned_pages = [q for q in fr.scanned_pages if q < boq[0]]
    return out


def boq_range(doc: ParsedDoc, sections: dict[str, tuple[int, int]]) -> tuple[int, int] | None:
    s, e = sections.get("商务文件", (1, doc.n_pages))
    pages = [p for p in range(s, e + 1) if any(_BOQ_HEADER_RE.search(ln) for ln in _page_head_lines(doc, p, 6))]
    if not pages:
        return None
    return (min(pages) - 1 if min(pages) > s and "封" in doc.page_text(min(pages) - 1)[:5] else min(pages), max(pages))


def tech_segments(doc: ParsedDoc, sections: dict[str, tuple[int, int]]) -> list[TechSegment]:
    if "技术文件" not in sections:
        return []
    s, e = sections["技术文件"]
    segs: list[TechSegment] = []
    for p in range(s, e + 1):
        first = _page_head_lines(doc, p, 1)
        if not first:
            continue
        m = _DANGER_RE.match(to_halfwidth(first[0]))
        if m:
            title = re.sub(r"\s+", "", first[0])
            segs.append(TechSegment(title=title, page_start=p, page_end=e))
    for i, sg in enumerate(segs):
        if i + 1 < len(segs):
            sg.page_end = max(sg.page_start, segs[i + 1].page_start - 1)
    return segs


def cover_identity(doc: ParsedDoc, page: int) -> tuple[str, str | None]:
    t = to_halfwidth(doc.page_text(page))
    b = _COVER_BIDDER_RE.search(t)
    l = _COVER_LEGAL_RE.search(t)
    return (re.sub(r"\s+", "", b.group(1)) if b else ""), (re.sub(r"\s+", "", l.group(1)) if l else None)


def build_bid_index(doc: ParsedDoc, req: TenderRequirements, profile: Profile) -> BidIndex:
    if profile.key == "fujian_gov_cs":
        from services.fujian_check.bid.gov_cs import build_gov_cs_index

        return build_gov_cs_index(doc, req, profile)
    idx = BidIndex(docx_mode=(doc.kind == "docx"))
    idx.sections = detect_sections(doc, profile)
    if not idx.sections:
        idx.parse_warnings.append("未识别到任何节标题（资格文件/商务文件/定标文件）")
    idx.forms = locate_forms(doc, idx.sections, req)
    idx.scanned_pages = [p.page for p in doc.pages if p.is_scanned]
    for fr in idx.forms:
        if fr.page_start is None:
            continue
        title = fr.form.title if fr.form else fr.matched_title
        for q in fr.scanned_pages:
            idx.scanned_attribution[q] = title
    idx.boq_page_range = boq_range(doc, idx.sections)
    idx.tech_segments = tech_segments(doc, idx.sections)

    # 投标人 / 法定代表人：任一节封面
    for name in ("资格文件", "商务文件", "定标文件"):
        if name in idx.sections:
            s, _ = idx.sections[name]
            for p in range(s, min(s + 3, doc.n_pages) + 1):
                bidder, legal = cover_identity(doc, p)
                if bidder:
                    idx.bidder_name = bidder
                    idx.legal_rep = idx.legal_rep or legal
                    break
        if idx.bidder_name:
            break

    from services.fujian_check.bid.bid_letter import parse_bid_letter, parse_boq_cover, parse_boq_summary
    from services.fujian_check.bid.personnel import parse_personnel
    from services.fujian_check.tender.appendix import find_appendix_pages, parse_appendix

    letter_fr = idx.form("投标函")
    if letter_fr and letter_fr.page_start:
        idx.letter = parse_bid_letter(doc, letter_fr.page_start, letter_fr.page_end or letter_fr.page_start)
        if idx.letter and not idx.letter.bidder:
            idx.letter.bidder = idx.bidder_name
    else:
        idx.parse_warnings.append("未找到投标函")

    if "商务文件" in idx.sections:
        s, e = idx.sections["商务文件"]
        pages = find_appendix_pages(doc, range(s, e + 1))
        idx.appendix = parse_appendix(doc, pages, "bid", "第2节 商务文件")
        idx.boq_cover = parse_boq_cover(doc, idx.boq_page_range)
        idx.boq_summary = parse_boq_summary(doc, idx.boq_page_range)

    idx.personnel = parse_personnel(doc, idx)
    _collect_dates_and_ids(doc, idx)
    return idx


_ACCOUNT_RE = re.compile(r"账\s*号\s*[:：]?\s*(\d{10,25})")
_LEGAL_ID_RE = re.compile(r"身份证号码?\s*[:：]?\s*(\d{17}[\dXx])")
_LEGAL_NAME_RE = re.compile(r"姓\s*名\s*[:：]?\s*([一-龥]{2,4})\s*性\s*别")
_DATE_FORMS_SKIP = ("工程量清单", "简要情况表", "管理人员表", "业绩", "基本情况表", "基本账户", "编制人员", "相关信息表", "联合体协议")


def _collect_dates_and_ids(doc: ParsedDoc, idx: BidIndex) -> None:
    """各表单文字层落款日期、基本账户账号、法定代表人身份证号。"""
    from services.fujian_check.dates import last_date
    from services.fujian_check.models import Sourced

    for fr in idx.forms:
        if fr.page_start is None or fr.match_kind not in ("exact", "alias", "fuzzy"):
            continue
        title = fr.form.title if fr.form else fr.matched_title
        end = fr.page_end or fr.page_start
        if "基本账户" in title:
            m = _ACCOUNT_RE.search(to_halfwidth("\n".join(doc.page_text(p) for p in range(fr.page_start, end + 1))))
            if m:
                idx.basic_account = m.group(1)
        if "法定代表人" in title and "证明" in title:
            t0 = to_halfwidth(doc.page_text(fr.page_start))
            m = _LEGAL_ID_RE.search(t0)
            if m:
                idx.legal_rep_id = m.group(1).upper()
            m = _LEGAL_NAME_RE.search(t0)
            if m and "系" in t0 and "法定代表人" in t0:
                idx.legal_rep = m.group(1)       # 资格证明书里的才是法定代表人；封面签字人可能是委托代理人
        if any(k in title for k in _DATE_FORMS_SKIP):
            continue
        if end - fr.page_start > 6:      # 长表单（履约承诺书 3 页以内）只看前后 3 页
            pages = list(range(fr.page_start, fr.page_start + 3)) + list(range(end - 2, end + 1))
        else:
            pages = list(range(fr.page_start, end + 1))
        found = None
        for p in pages:
            t = doc.page_text(p)
            if doc.pages[p - 1].is_scanned:
                continue
            d = last_date(t)
            if d and ("日期" in t or "年" in t or "-" in t):
                found = (d, p)
        if found:
            sec = fr.form.section if fr.form else None
            idx.form_dates[title] = Sourced(value=found[0], loc=Location(doc="bid", page=found[1], section=sec, form=title, excerpt=found[0]))
