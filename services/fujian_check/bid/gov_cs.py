"""政府采购磋商响应文件索引：附件1-8 定位、开标一览表取值、偏离表填写情况、承诺函检索、人员页定位。"""
from __future__ import annotations

import re

from services.fujian_check.models import BidIndex, BidLetter, FormRange, Location, Money, ParsedDoc, Person, PersonnelTable, Sourced, TechSegment, TenderRequirements
from services.fujian_check.profiles.base import Profile
from services.fujian_check.tender._tables import tables_for_pages
from services.fujian_check.textnorm import money, to_halfwidth

_ATT_RE = re.compile(r"^附件\s*(\d+(?:-\d+){0,2})\s*[:：]?\s*(.*)$")
_KNOWN_TITLES = {
    "开标(报价)一览表": "2", "开标（报价）一览表": "2", "财务状况报告": "3-4-2", "资格承诺函": "3-4-1",
    "中小企业声明函": "7-1-1", "信用中国查询结果": "3-5", "依法缴纳税收证明材料": "3-4-2", "依法缴纳社会保障资金证明材料": "3-4-2",
}
# 招标文件里"未提供…按废标/无效"类专项声明的主题词 → 响应文件检索键（_COMMIT_KEYS）
DECL_TOPICS = {
    "团意险": "团意险专项声明", "团体意外": "团意险专项声明", "意外伤害": "团意险专项声明",
    "代理服务费": "代理服务费专项保证函",
    "分包": "不分包转包承诺", "转包": "不分包转包承诺",
    "缺陷责任期": "缺陷责任期承诺", "质保期": "缺陷责任期承诺",
    "农民工": "农民工工资承诺",
    "安全责任": "安全责任承诺", "安全事故": "安全责任承诺",
    "横道图": "横道图进度表", "进度表": "横道图进度表",
    "兼任": "不兼岗承诺", "兼岗": "不兼岗承诺",
    "在岗": "本单位在岗承诺",
}
_COMMIT_KEYS = {
    "团意险专项声明": r"团意险|团体意外|意外伤害保险",
    "代理服务费专项保证函": r"代理服务费.{0,40}(保证函|承诺)|保证函.{0,40}代理服务费",
    "不兼岗承诺": r"不得相互兼任|不可兼岗|不得兼岗|不重复使用|不得重复使用",
    "本单位在岗承诺": r"本单位在岗|本企业在岗",
    "横道图进度表": r"横道图",
    "安全责任承诺": r"安全责任.{0,20}(自行负责|负全责)|安全事故.{0,10}负全责",
    "不分包转包承诺": r"不(进行|得|会)?(分包|转包)|不分包|不转包",
    "缺陷责任期承诺": r"缺陷责任期.{0,10}(\d+)\s*个月",
    "农民工工资承诺": r"农民工工资",
}


def _first_line(doc: ParsedDoc, p: int) -> str:
    for ln in doc.page_text(p).split("\n"):
        if ln.strip():
            return to_halfwidth(ln.strip())
    return ""


def _is_toc(doc: ParsedDoc, p: int) -> bool:
    """目录页：多行以"附件N"开头且几乎没有正文。"""
    lines = [to_halfwidth(ln.strip()) for ln in doc.page_text(p).split("\n") if ln.strip()]
    hits = sum(1 for ln in lines if _ATT_RE.match(ln))
    return hits >= 4 and hits / max(1, len(lines)) >= 0.4


def locate_attachments(doc: ParsedDoc, req: TenderRequirements) -> list[FormRange]:
    required = req.forms_in("响应文件")
    found: dict[str, tuple[int, str]] = {}
    for p in range(1, doc.n_pages + 1):
        if doc.pages[p - 1].is_scanned or _is_toc(doc, p):
            continue
        head = _first_line(doc, p)
        m = _ATT_RE.match(head)
        no = None
        title = head
        if m:
            no = m.group(1)
            title = m.group(2) or head
        else:
            for k, v in _KNOWN_TITLES.items():
                if head.startswith(k):
                    no = v
                    break
        if no and no not in found:
            found[no] = (p, title[:40])
    out: list[FormRange] = []
    ordered = sorted(found.items(), key=lambda kv: kv[1][0])
    for i, (no, (p, title)) in enumerate(ordered):
        end = ordered[i + 1][1][0] - 1 if i + 1 < len(ordered) else doc.n_pages
        f = next((x for x in required if x.no == no), None)
        scanned = [q for q in range(p, end + 1) if doc.pages[q - 1].is_scanned]
        out.append(FormRange(form=f, matched_title=title, page_start=p, page_end=max(p, end), scanned_pages=scanned,
                             match_kind="exact" if f else "alias"))
    for f in required:
        if f.no not in found:
            # 父附件（如"5 技术和商务偏离表"）由子附件（5-1/5-2）代表
            children = [fr for fr in out if fr.form and fr.form.no.startswith(f.no + "-")]
            if children:
                out.append(FormRange(form=f, matched_title="由子附件代表", page_start=children[0].page_start, page_end=children[-1].page_end,
                                     scanned_pages=[], match_kind="alias"))
                continue
            # 子附件缺失：父附件或紧邻的前一个兄弟附件范围内若有扫描页 → 疑似扫描件提交
            parent = f.no.split("-")[0]
            cands = [fr for fr in out if fr.form and (fr.form.no == parent or fr.form.no.startswith(parent + "-")) and fr.scanned_pages]
            prev_sib = None
            for fr in sorted(cands, key=lambda x: x.page_start or 0):
                if fr.form.no < f.no:
                    prev_sib = fr
            pr = prev_sib or (cands[0] if cands else None)
            if pr and pr.scanned_pages:
                out.append(FormRange(form=f, matched_title="", page_start=min(pr.scanned_pages), page_end=max(pr.scanned_pages),
                                     scanned_pages=pr.scanned_pages, match_kind="scanned"))
            else:
                out.append(FormRange(form=f, matched_title="", match_kind="missing"))
    return out


def parse_price_sheet(doc: ParsedDoc, page: int) -> BidLetter:
    L = BidLetter(loc=Location(doc="bid", page=page, section="响应文件", form="开标（报价）一览表"))
    for tb in tables_for_pages(doc, [page]).get(page, []):
        header = [re.sub(r"\s+", "", c) for c in tb[0]]
        for row in tb[1:]:
            cells = [re.sub(r"\s+", "", c) for c in row]
            if not cells or not cells[0].isdigit():
                continue
            for i, h in enumerate(header):
                v = cells[i] if i < len(cells) else ""
                v = v.strip("{}")
                if "响应报价" in h:
                    L.total_price = Money(value=money(v + "元") or 0, raw=v)
                elif h.startswith("项目经理"):
                    L.pm_name = v[:6]
                elif "执业证书" in h:
                    L.pm_cert_no = v
                elif "施工工期" in h:
                    mm = re.search(r"(\d{2,4})\s*(?:个)?(?:日历)?[日天]", v)
                    if mm:
                        L.duration_days = int(mm.group(1))
            break
    t = to_halfwidth(doc.page_text(page)).replace(" ", "")
    m = re.search(r"合计响应报价[:：]?\s*([\d,\.]+)", t)
    if m and L.total_price is None:
        L.total_price = Money(value=money(m.group(1) + "元") or 0, raw=m.group(1))
    return L


def deviation_table_stats(doc: ParsedDoc, page: int, end: int) -> dict[str, int | list[int]]:
    rows = 0
    negative: list[int] = []
    for p in range(page, min(end, page + 3) + 1):
        for tb in tables_for_pages(doc, [p]).get(p, []):
            header = "".join(tb[0]).replace(" ", "")
            if "是否偏离" not in header:
                continue
            for row in tb[1:]:
                cells = [c.strip() for c in row]
                if any(cells):
                    rows += 1
                    if any(re.search(r"负偏离|不满足|不响应", c) for c in cells):
                        negative.append(p)
    return {"rows": rows, "negative_pages": negative}


def find_commitments(doc: ParsedDoc, skip: tuple[int, int] | None = None) -> dict[str, Sourced]:
    out: dict[str, Sourced] = {}
    for key, pat in _COMMIT_KEYS.items():
        rx = re.compile(pat)
        for p in range(1, doc.n_pages + 1):
            t = re.sub(r"\s+", "", to_halfwidth(doc.page_text(p)))
            m = rx.search(t)
            if m:
                s = max(0, m.start() - 40)
                out[key] = Sourced(value=t[s:m.end() + 60], loc=Location(doc="bid", page=p, section="响应文件", excerpt=t[s:m.end() + 120]))
                break
    return out


def find_personnel_pages(doc: ParsedDoc) -> list[int]:
    hits = []
    for p in range(1, doc.n_pages + 1):
        t = doc.page_text(p)
        if ("项目负责人" in t or "项目经理" in t) and re.search(r"注册建造师|安全生产考核|职称|养老保险|身份证", t):
            hits.append(p)
    return hits


def build_gov_cs_index(doc: ParsedDoc, req: TenderRequirements, profile: Profile) -> BidIndex:
    idx = BidIndex(docx_mode=(doc.kind == "docx"))
    idx.sections = {"响应文件": (1, doc.n_pages)}
    idx.forms = locate_attachments(doc, req)
    idx.scanned_pages = [p.page for p in doc.pages if p.is_scanned]
    for fr in idx.forms:
        if fr.page_start:
            title = fr.form.title if fr.form else fr.matched_title
            for q in fr.scanned_pages:
                idx.scanned_attribution.setdefault(q, title)
    # 封面
    t1 = to_halfwidth(doc.page_text(1))
    m = re.search(r"供应商名称\s*[:：]?\s*\n?\s*([一-龥（）()]{4,40}?(?:公司|集团))", t1)
    if m:
        idx.bidder_name = m.group(1)
    if not idx.bidder_name:
        for p in range(1, min(12, doc.n_pages) + 1):
            m = re.search(r"供应商(?:名称)?\s*[:：]\s*([一-龥（）()]{4,40}?(?:公司|集团))", to_halfwidth(doc.page_text(p)))
            if m:
                idx.bidder_name = m.group(1)
                break
    m = re.search(r"法定代表人\s*[（(]?负责人[)）]?\s*[:：]\s*([一-龥]{2,4})", "\n".join(doc.page_text(p) for p in range(1, min(80, doc.n_pages) + 1)))
    if m:
        idx.legal_rep = m.group(1)
    # 开标一览表
    ps = next((fr for fr in idx.forms if fr.form and fr.form.no == "2" and fr.page_start), None) or \
        next((fr for fr in idx.forms if "开标" in fr.matched_title and fr.page_start), None)
    if ps:
        idx.letter = parse_price_sheet(doc, ps.page_start)
        idx.letter.bidder = idx.bidder_name
    # 响应声明/响应函里的签字代表：在附件1 的页里找，找不到再扫前 12 页
    decl = next((fr for fr in idx.forms if fr.form and fr.form.no == "1" and fr.page_start), None) or \
        next((fr for fr in idx.forms if fr.page_start and re.search(r"响应声明|响应函", fr.matched_title)), None)
    cand_pages = list(range(decl.page_start, (decl.page_end or decl.page_start) + 1))[:4] if decl else list(range(1, min(12, doc.n_pages) + 1))
    for p in cand_pages:
        t = re.sub(r"\s+", "", to_halfwidth(doc.page_text(p)))
        m = re.search(r"签字代表([一-龥]{2,4})[、,，(（]", t) or re.search(r"供应商代表[:：]?([一-龥]{2,4})\(?签字", t)
        if m:
            if idx.letter is None:
                idx.letter = BidLetter(loc=Location(doc="bid", page=p, section="响应文件", form="磋商响应声明"))
            idx.letter.signer = m.group(1)
            break
    # 偏离表
    for no, key in (("5-1", "偏离表5-1"), ("5-2", "偏离表5-2")):
        fr = next((f for f in idx.forms if f.form and f.form.no == no and f.page_start), None)
        if fr:
            st = deviation_table_stats(doc, fr.page_start, fr.page_end or fr.page_start)
            idx.appendix[key] = Sourced(value=f"rows={st['rows']};negative={st['negative_pages']}",
                                        loc=Location(doc="bid", page=fr.page_start, section="响应文件", form=f"附件{no}"))
    # 承诺函
    for k, v in find_commitments(doc).items():
        idx.appendix[f"承诺:{k}"] = v
    # 人员页
    pages = find_personnel_pages(doc)
    if pages:
        idx.personnel = PersonnelTable(loc=Location(doc="bid", page=pages[0], page_end=pages[-1], section="响应文件", form="项目团队人员"))
        for p in pages[:6]:
            t = to_halfwidth(doc.page_text(p))
            for role in ("项目负责人", "项目经理", "技术负责人", "施工员", "质量员", "材料员", "机械员", "安全员"):
                for mm in re.finditer(role + r"[^一-龥]{0,6}(?:姓名)?[:：]?\s*([一-龥]{2,3})(?![一-龥])", t):
                    nm = mm.group(1)
                    if re.search(r"[即提供须负责员职称证书应本项配备名称]", nm):
                        continue
                    idx.personnel.people.append(Person(name=nm, post=role, page=p))
        # 去重
        seen = set()
        uniq = []
        for x in idx.personnel.people:
            if (x.name, x.post) not in seen:
                seen.add((x.name, x.post))
                uniq.append(x)
        idx.personnel.people = uniq
    # 技术方案段落（评分方案项）
    for p in range(1, doc.n_pages + 1):
        head = _first_line(doc, p)
        if re.match(r"^(第[一二三四五六七八九十]+[章节]|[一二三四五六七八九十]+、).{0,30}(项目概况|施工方案|质量|安全|文明施工|进度)", head):
            idx.tech_segments.append(TechSegment(title=head[:40], page_start=p, page_end=p))
    return idx
