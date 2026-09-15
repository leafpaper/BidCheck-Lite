"""人员表：拟派出施工现场管理人员表（平台打印件）+ 项目负责人/技术负责人简要情况表。"""
from __future__ import annotations

import re

from services.fujian_check.models import BidIndex, Location, ParsedDoc, Person, PersonnelTable
from services.fujian_check.tender._tables import tables_for_pages
from services.fujian_check.textnorm import to_halfwidth

POSTS = [
    "项目负责人", "项目副经理", "项目技术负责人", "技术负责人",
    "设备安装施工员", "土建施工员", "施工员", "土建质量员", "质量员", "材料员", "机械员",
    "安全员", "劳务员", "资料员", "标准员", "测量员", "试验员", "造价员", "预算员",
]
_POST_RE = re.compile(r"^(" + "|".join(POSTS) + r")(?:\s*[（(]如有[)）])?$")
_NAME_RE = re.compile(r"^[一-龥·]{2,4}$")
_NOT_NAMES = {"岗位名称", "姓名", "职称类型", "职称编号", "职称专业", "注册编号", "注册专业", "岗位证书", "类型及等级", "备注",
              "如有", "序号", "岗位证书类型", "岗位证书编号", "执业注册"}
_ID_RE = re.compile(r"\d{17}[\dXx]")


def _parse_staff_table_text(doc: ParsedDoc, p0: int, p1: int) -> list[Person]:
    people: list[Person] = []
    for p in range(p0, p1 + 1):
        lines = [to_halfwidth(ln).strip() for ln in doc.page_text(p).split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            m = _POST_RE.match(ln)
            if not m:
                continue
            # 岗位名称后面紧跟姓名；表头行"岗位名称 姓名"不会命中 _POST_RE
            for j in range(i + 1, min(i + 3, len(lines))):
                if _NAME_RE.match(lines[j]) and lines[j] not in _NOT_NAMES and not _POST_RE.match(lines[j]):
                    cert = lines[j + 1] if j + 1 < len(lines) else None
                    people.append(Person(name=lines[j], post=m.group(1), cert=cert, page=p))
                    break
    # 去重（同名同岗）
    seen: set[tuple[str, str]] = set()
    uniq: list[Person] = []
    for x in people:
        k = (x.name, x.post)
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


def _enrich_from_form(doc: ParsedDoc, p0: int, p1: int, post: str) -> Person | None:
    """从"拟派出项目负责人/技术负责人简要情况表"抽姓名/身份证/证号/职称。"""
    tables = tables_for_pages(doc, list(range(p0, min(p1, p0 + 1) + 1)))
    kv: dict[str, str] = {}
    for p, tbls in tables.items():
        for tb in tbls:
            for row in tb:
                cells = [c.replace(" ", "") for c in row]
                for i, c in enumerate(cells):
                    if c and i + 1 < len(cells) and cells[i + 1]:
                        kv.setdefault(c, cells[i + 1])
                    elif c and i + 3 < len(cells) and not cells[i + 1] and cells[i + 3]:
                        kv.setdefault(c, cells[i + 3])
    name = kv.get("姓名") or kv.get("姓 名")
    if not name:
        t = to_halfwidth(doc.page_text(p0))
        m = re.search(r"姓\s*名\s*([一-龥]{2,4})", t)
        name = m.group(1) if m else None
    if not name:
        return None
    id_no = next((v for k, v in kv.items() if "身份证" in k and _ID_RE.search(v)), None)
    if id_no:
        id_no = _ID_RE.search(id_no).group(0)  # type: ignore[union-attr]
    cert = kv.get("注册建造师执业资格等级") or kv.get("职称")
    cert_no = kv.get("建造师注册编号") or kv.get("职称证书编号")
    title = kv.get("职称")
    return Person(name=name, post=post, id_no=id_no, cert=cert, cert_no=cert_no, title=title, page=p0)


def parse_personnel(doc: ParsedDoc, idx: BidIndex) -> PersonnelTable | None:
    table = PersonnelTable()
    fr = idx.form("施工现场管理人员表") or idx.form("施工管理人员表")
    if fr and fr.page_start:
        table.people = _parse_staff_table_text(doc, fr.page_start, fr.page_end or fr.page_start)
        table.loc = Location(doc="bid", page=fr.page_start, page_end=fr.page_end, section="第1节 资格文件",
                             form=fr.form.title if fr.form else fr.matched_title)
    pm_fr = idx.form("项目负责人简要情况表")
    if pm_fr and pm_fr.page_start:
        pm = _enrich_from_form(doc, pm_fr.page_start, pm_fr.page_end or pm_fr.page_start, "项目负责人")
        if pm:
            existing = next((x for x in table.people if x.post == "项目负责人"), None)
            if existing:
                existing.id_no = existing.id_no or pm.id_no
                existing.cert_no = existing.cert_no or pm.cert_no
                existing.title = existing.title or pm.title
                if existing.name != pm.name:
                    idx.parse_warnings.append(f"项目负责人姓名不一致: 人员表 {existing.name} vs 简要情况表 {pm.name}")
            else:
                table.people.append(pm)
    tl_fr = idx.form("技术负责人简要情况表")
    if tl_fr and tl_fr.page_start:
        tl = _enrich_from_form(doc, tl_fr.page_start, tl_fr.page_end or tl_fr.page_start, "项目技术负责人")
        if tl:
            existing = next((x for x in table.people if "技术负责人" in x.post), None)
            if existing:
                existing.id_no = existing.id_no or tl.id_no
                existing.title = existing.title or tl.title
            else:
                table.people.append(tl)
    return table if table.people else None
