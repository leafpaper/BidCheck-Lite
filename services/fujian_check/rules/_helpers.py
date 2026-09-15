"""规则共用小工具。"""
from __future__ import annotations

import re

from services.fujian_check.models import FormRange, Location, Money
from services.fujian_check.rules.base import RuleContext
from services.fujian_check.textnorm import strip_project_name, to_halfwidth

_LEVEL_ORDER = {"特级": 0, "壹级": 1, "一级": 1, "贰级": 2, "二级": 2, "叁级": 3, "三级": 3}


def norm(s: str | None) -> str:
    return strip_project_name(s or "")


def level_rank(s: str | None) -> int | None:
    """资质/建造师等级 → 数字（越小越高）。"""
    if not s:
        return None
    t = to_halfwidth(s).replace(" ", "")
    for k, v in _LEVEL_ORDER.items():
        if k in t:
            return v
    m = re.search(r"([一二三壹贰叁])\s*级", t)
    if m:
        return _LEVEL_ORDER.get(m.group(1) + "级")
    return None


def fmt_money(m: Money | None) -> str:
    return f"{m.value:,} 元" if m else "—"


def pct(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", to_halfwidth(s))
    return float(m.group(1)) if m else None


def money_set(*vals: Money | None) -> set[int]:
    return {v.value for v in vals if v is not None}


def form_status(fr: FormRange) -> str:
    return {"exact": "已定位", "alias": "已定位(别名)", "fuzzy": "已定位(近似)", "scanned": "疑似扫描件提交",
            "missing": "未找到"}[fr.match_kind]


def page_contains(ctx: RuleContext, pages: range | list[int], pattern: str) -> Location | None:
    rx = re.compile(pattern)
    for p in pages:
        t = ctx.bdoc.page_text(p)
        m = rx.search(t)
        if m:
            s = max(0, m.start() - 60)
            return ctx.bid_page_loc(p, None, t[s:m.end() + 100])
    return None


def jv_filled(ctx: RuleContext) -> bool:
    """联合体协议书是否实际填写（独立投标时该表通常整页为"/"或空模板）。"""
    fr = ctx.form("联合体协议书")
    if not fr or not fr.page_start:
        return False
    text = ctx.bdoc.page_text(fr.page_start)
    body = re.sub(r"\s+", "", text)
    slashes = body.count("/")
    names = re.findall(r"(?:牵头人|成员|甲方|乙方)[:：]?([一-龥（）()]{4,40}?(?:公司|集团|局|院|所))", body)
    names = [n for n in names if n != ctx.idx.bidder_name.replace(" ", "")]
    return bool(names) and slashes < 3


def cover_pages(ctx: RuleContext) -> list[int]:
    out = []
    for name in ("资格文件", "商务文件", "定标文件"):
        if name in ctx.idx.sections:
            s, _ = ctx.idx.sections[name]
            out += list(range(s, min(s + 3, ctx.bdoc.n_pages) + 1))
    return out
