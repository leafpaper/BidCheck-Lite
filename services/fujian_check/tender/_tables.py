"""前附表 / 数据表 共用：4 列编号表（项号|条款号|条款名称|编列内容）的抽取与跨页拼接。"""
from __future__ import annotations

import re

from services.fujian_check import cache
from services.fujian_check.models import Location, ParsedDoc
from services.fujian_check.pdf_reader import extract_tables

_ITEM_RE = re.compile(r"^\d{1,2}\.?$")
_HEADER_HINT = ("项号", "条款号", "条款名称", "编列内容")


class NumberedRow:
    __slots__ = ("item_no", "clause", "name", "content", "page", "page_end")

    def __init__(self, item_no: int, clause: str, name: str, content: str, page: int):
        self.item_no = item_no
        self.clause = clause
        self.name = name
        self.content = content
        self.page = page
        self.page_end = page


def _is_header(row: list[str]) -> bool:
    joined = "".join(c.replace(" ", "") for c in row)
    return sum(1 for h in _HEADER_HINT if h in joined) >= 2


def tables_for_pages(doc: ParsedDoc, pages: list[int]) -> dict[int, list[list[list[str]]]]:
    """带缓存的 pdfplumber 表格抽取；docx 直接用读取器已结构化的表格（page = 合成页号）。"""
    if doc.kind == "docx":
        return {p: [t.rows for t in doc.tables if t.page == p] for p in pages}
    out: dict[int, list[list[list[str]]]] = {}
    missing: list[int] = []
    for p in pages:
        cached = cache.tables_cache_get(doc.file_hash, p)
        if cached is None:
            missing.append(p)
        else:
            out[p] = cached
    if missing:
        fresh = extract_tables(doc.path, missing)
        for p, tbls in fresh.items():
            out[p] = tbls
            cache.tables_cache_put(doc.file_hash, p, tbls)
    return out


def parse_numbered_table(doc: ParsedDoc, pages: list[int]) -> list[NumberedRow]:
    """把连续页上的 4 列编号表拼成行列表；跨页续行（项号为空）并入上一行。"""
    tables = tables_for_pages(doc, pages)
    rows: list[NumberedRow] = []
    for p in pages:
        for tb in tables.get(p, []):
            for raw in tb:
                cells = [c.strip() for c in raw] + [""] * (4 - len(raw))
                if _is_header(cells):
                    continue
                item, clause, name, content = cells[0], cells[1], cells[2], " ".join(cells[3:]).strip()
                if _ITEM_RE.match(item):
                    rows.append(NumberedRow(int(item.rstrip(".")), clause, name, content, p))
                elif rows and not item:
                    # 续行：条款名称/内容并入上一行
                    prev = rows[-1]
                    if clause and not prev.clause:
                        prev.clause = clause
                    if name:
                        prev.name = (prev.name + " " + name).strip()
                    if content:
                        prev.content = (prev.content + " " + content).strip()
                    prev.page_end = p
    # 名称里的双写残留（"资金来源和落实情 况况"）：按词去重
    from services.fujian_check.textnorm import dedupe_doubled_in_line

    for r in rows:
        r.name = re.sub(r"\s+", "", dedupe_doubled_in_line(r.name))
    return rows


def row_location(r: NumberedRow, section_label: str, clause_prefix: str = "") -> Location:
    return Location(
        doc="tender",
        page=r.page,
        page_end=r.page_end,
        section=section_label,
        clause=f"{clause_prefix}{r.clause}" if r.clause else f"第{r.item_no}项",
        excerpt=(r.name + "：" + r.content)[:300],
    )
