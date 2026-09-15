"""日期 / 身份证小工具：全部纯函数，供解析层与规则共用。"""
from __future__ import annotations

import re
from datetime import date, timedelta

_DATE_RE = re.compile(r"(20\d{2})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*日?")
_ID_RE = re.compile(r"\d{17}[\dXx]")
_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CHECK = "10X98765432"


def parse_date(s: str | None) -> date | None:
    """'2026-08-13 09:10:00' / '2026年8月12日' / '2026.7.30' → date；非法日期返回 None。"""
    if not s:
        return None
    m = _DATE_RE.search(s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def find_dates(text: str) -> list[str]:
    """文本中所有合法日期，ISO 字符串，按出现顺序。"""
    out: list[str] = []
    for y, mo, d in _DATE_RE.findall(text or ""):
        try:
            out.append(date(int(y), int(mo), int(d)).isoformat())
        except ValueError:
            continue
    return out


def find_invalid_dates(text: str) -> list[str]:
    """形似日期但不合法（2月30日等）的原文片段。"""
    out: list[str] = []
    for m in _DATE_RE.finditer(text or ""):
        try:
            date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            out.append(m.group(0))
    return out


def last_date(text: str) -> str | None:
    ds = find_dates(text)
    return ds[-1] if ds else None


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def find_months(text: str) -> list[str]:
    """'2026年3月' / '2026-03' / '202603' → 'YYYY-MM'（去重保序）。"""
    out: list[str] = []
    for y, m in re.findall(r"(20\d{2})\s*[年\-/.]\s*(\d{1,2})(?!\d)", text or ""):
        if 1 <= int(m) <= 12:
            k = f"{y}-{int(m):02d}"
            if k not in out:
                out.append(k)
    for y, m in re.findall(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)", text or ""):
        k = f"{y}-{m}"
        if k not in out:
            out.append(k)
    return out


_MONTH_RANGE_RE = re.compile(r"(20\d{2})\s*[年\-/.]\s*(\d{1,2})\s*月?\s*(?:\d{1,2}\s*日?)?\s*[至到~\-—]+\s*(20\d{2})\s*[年\-/.]\s*(\d{1,2})")


def months_covered(text: str) -> list[str]:
    """find_months + 展开「2026年1月至2026年6月」这类区间，得到全部 YYYY-MM。"""
    out = list(find_months(text))
    for y1, m1, y2, m2 in _MONTH_RANGE_RE.findall(text or ""):
        try:
            a, b = date(int(y1), int(m1), 1), date(int(y2), int(m2), 1)
        except ValueError:
            continue
        cur = a
        while cur <= b and len(out) < 120:
            k = month_key(cur)
            if k not in out:
                out.append(k)
            cur = add_months(cur, 1)
    return out


def month_window(deadline: date, start_offset: int, months: int) -> list[str]:
    """截止日「上 start_offset 个月」为终点、往前 months 个月的 YYYY-MM 列表（含端点）。"""
    end = add_months(date(deadline.year, deadline.month, 1), -start_offset)
    return [month_key(add_months(end, -i)) for i in range(months)][::-1]


def id_valid(id_no: str | None) -> bool | None:
    """18 位身份证校验位；不是 18 位返回 None（不判）。"""
    if not id_no:
        return None
    s = id_no.strip().upper()
    if not _ID_RE.fullmatch(s):
        return None
    total = sum(int(c) * w for c, w in zip(s[:17], _ID_WEIGHTS))
    return _ID_CHECK[total % 11] == s[17]


def birth_from_id(id_no: str | None) -> date | None:
    if not id_no or not _ID_RE.fullmatch(id_no.strip().upper()):
        return None
    try:
        return date(int(id_no[6:10]), int(id_no[10:12]), int(id_no[12:14]))
    except ValueError:
        return None


def age_at(id_no: str | None, on: date | None) -> int | None:
    b = birth_from_id(id_no)
    if b is None or on is None:
        return None
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def iso(d: date | None) -> str:
    return d.isoformat() if d else "—"


def days_after(d: date, n: int) -> date:
    return d + timedelta(days=n)
