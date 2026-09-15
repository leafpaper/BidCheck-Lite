"""证据检索：表单优先 → 节 → 关键词窗口 → 全文降权。LLM 只吃窗口。"""
from __future__ import annotations

import re
from typing import Iterable

from services.fujian_check.models import EvidenceWindow, Location
from services.fujian_check.rules.base import RuleContext


def _pages_for(ctx: RuleContext, form: str | None, section: str | None, pages: Iterable[int] | None) -> list[int]:
    if pages is not None:
        return list(pages)
    if form:
        fr = ctx.form(form)
        if fr and fr.page_start:
            return list(range(fr.page_start, (fr.page_end or fr.page_start) + 1))
    if section and section in ctx.idx.sections:
        s, e = ctx.idx.sections[section]
        return list(range(s, e + 1))
    return []


def locate(ctx: RuleContext, *, form: str | None = None, section: str | None = None,
           patterns: Iterable[str] = (), pages: Iterable[int] | None = None,
           window_chars: int = 1200, max_windows: int = 4, allow_global: bool = True) -> list[EvidenceWindow]:
    pats = [re.compile(p) for p in patterns]
    cand_pages = _pages_for(ctx, form, section, pages)
    scope_global = False
    if not cand_pages and allow_global:
        cand_pages = list(range(1, ctx.bdoc.n_pages + 1))
        scope_global = True
        max_windows = min(max_windows, 2)
    windows: list[EvidenceWindow] = []
    if not pats:
        # 无关键词：整段表单文本切窗
        for p in cand_pages[:6]:
            text = ctx.bdoc.page_text(p)
            if text.strip():
                windows.append(EvidenceWindow(loc=ctx.bid_page_loc(p, form, text[:200]), text=text[:window_chars],
                                              score=1.0))
        return windows[:max_windows]
    for p in cand_pages:
        text = ctx.bdoc.page_text(p)
        if not text:
            continue
        hits = [m.start() for rx in pats for m in rx.finditer(text)]
        if not hits:
            continue
        hits.sort()
        # 以首个命中为中心切窗，窗口内命中密度为分数
        start = max(0, hits[0] - window_chars // 3)
        chunk = text[start:start + window_chars]
        density = sum(1 for h in hits if start <= h < start + window_chars)
        score = density * (0.5 if scope_global else 1.0)
        windows.append(EvidenceWindow(loc=ctx.bid_page_loc(p, form, chunk[:200]), text=chunk, score=score))
    windows.sort(key=lambda w: (-w.score, w.loc.page or 0))
    return windows[:max_windows]


def first_hit_loc(ctx: RuleContext, pattern: str, *, form: str | None = None, section: str | None = None,
                  pages: Iterable[int] | None = None) -> Location | None:
    rx = re.compile(pattern)
    for p in _pages_for(ctx, form, section, pages) or range(1, ctx.bdoc.n_pages + 1):
        text = ctx.bdoc.page_text(p)
        m = rx.search(text)
        if m:
            s = max(0, m.start() - 60)
            return ctx.bid_page_loc(p, form, text[s:m.end() + 120])
    return None
