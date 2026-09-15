"""投标函附录（5 列表：序号|项目内容|合同条款号|约定内容|备注）→ {项目内容: Sourced(约定内容)}。

招标文件第8章与投标文件商务文件共用同一格式，故本模块两边通用。
"""
from __future__ import annotations

import re

from services.fujian_check.models import Location, ParsedDoc, Sourced
from services.fujian_check.tender._tables import tables_for_pages

_HEADER_KEYS = ("项目内容", "合同条款号", "约定内容")
_TITLE_RE = re.compile(r"^[一二三四五六七八九十]{0,3}、?\s*投标函附录")


def find_appendix_pages(doc: ParsedDoc, pages: range | list[int]) -> list[int]:
    """在给定页范围内找"投标函附录"标题页及其后连续的附录表页（最多 3 页）。"""
    for p in pages:
        if doc.pages[p - 1].is_toc:
            continue
        first = doc.page_text(p).split("\n")[:2]
        if any(_TITLE_RE.match(ln.strip()) for ln in first):
            out = [p]
            for q in (p + 1, p + 2):
                if q <= doc.n_pages and "约定内容" not in doc.page_text(q) and not re.search(r"\d\s*[\.．]\s*\d", doc.page_text(q)[:80]):
                    break
                if q <= doc.n_pages:
                    out.append(q)
            return out
    return []


def parse_appendix(doc: ParsedDoc, pages: list[int], which: str, section_label: str) -> dict[str, Sourced]:
    out: dict[str, Sourced] = {}
    if not pages:
        return out
    tables = tables_for_pages(doc, pages)
    for p in pages:
        for tb in tables.get(p, []):
            for row in tb:
                cells = [c.strip() for c in row] + [""] * (5 - len(row))
                if sum(1 for k in _HEADER_KEYS if k in "".join(cells)) >= 2:
                    continue
                seq, item, clause, content, note = cells[0], cells[1], cells[2], cells[3], cells[4]
                if not item or not content:
                    continue
                key = re.sub(r"\s+", "", item)
                clause_clean = re.sub(r"\s+", "", clause)
                out[key] = Sourced(
                    value=content + (f"（备注：{note}）" if note else ""),
                    loc=Location(doc=which, page=p, section=section_label, form="投标函附录",  # type: ignore[arg-type]
                                 clause=f"合同条款{clause_clean}" if clause_clean else None,
                                 excerpt=f"{key}：{content}"[:300]),
                )
    return out
