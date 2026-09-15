"""章 / 节定位。

规则：
- 章标题 `第X章 标题` 独占一行、行首；跳过目录页；同一章取首次出现（页码单调递增）。
- 节标题 `第X节 标题`，归属最近的章。
- 页范围 = 本标题起点到下一个同级标题起点前一页。
"""
from __future__ import annotations

import re

from services.fujian_check.models import ParsedDoc, Section
from services.fujian_check.textnorm import cn_to_int

_CH_RE = re.compile(r"^第\s*([一二三四五六七八九十]{1,2}|\d{1,2})\s*章\s*(\S.{0,40})?$")
_SEC_RE = re.compile(r"^第\s*([一二三四五六七八九十]{1,2}|\d{1,2})\s*节\s*(\S.{0,40})?$")


def _num(s: str) -> int | None:
    return int(s) if s.isdigit() else cn_to_int(s)


def locate_sections(doc: ParsedDoc, profile=None) -> list[Section]:
    chapters: list[Section] = []
    seen: set[int] = set()
    last_ch = 0
    for p, _order, line in doc.iter_lines():
        if doc.pages[p - 1].is_toc:
            continue
        m = _CH_RE.match(line)
        if not m:
            continue
        n = _num(m.group(1))
        if n is None or n in seen or n < last_ch:
            continue
        # 排除正文里"第2章 投标须知"式引用：要求该行是块首行（iter_lines 已逐行，用 line 本身判断即可）
        title = (m.group(2) or "").strip()
        chapters.append(Section(chapter=n, section=None, title=title, page_start=p, page_end=doc.n_pages))
        seen.add(n)
        last_ch = n
    for i, ch in enumerate(chapters):
        if i + 1 < len(chapters):
            ch.page_end = max(ch.page_start, chapters[i + 1].page_start - 1)

    sections: list[Section] = []
    for ch in chapters:
        subs: list[Section] = []
        seen_s: set[int] = set()
        for p, _o, line in doc.iter_lines(range(ch.page_start, ch.page_end + 1)):
            if doc.pages[p - 1].is_toc:
                continue
            m = _SEC_RE.match(line)
            if not m:
                continue
            n = _num(m.group(1))
            if n is None or n in seen_s:
                continue
            subs.append(Section(chapter=ch.chapter, section=n, title=(m.group(2) or "").strip(),
                                page_start=p, page_end=ch.page_end))
            seen_s.add(n)
        for i, s in enumerate(subs):
            if i + 1 < len(subs):
                s.page_end = max(s.page_start, subs[i + 1].page_start - 1)
        sections.append(ch)
        sections.extend(subs)
    return sections


def find_section(sections: list[Section], chapter: int, section: int | None = None) -> Section | None:
    for s in sections:
        if s.chapter == chapter and s.section == section:
            return s
    return None
