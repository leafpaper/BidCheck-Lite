"""文本归一化与数值解析。

覆盖福建平台版 PDF 的几个已知伪影：
- pdfplumber 加粗字形双写（"条条款款名名称称"）
- 竖排单字表格（"缺⏎陷⏎责⏎任⏎期"）
- 全角标点/数字/空格
- 中文大写金额 → 整数元；"34.65万元" → 346500
- 中文数字章节号（一、二…十 / 壹贰叁）
"""
from __future__ import annotations

import re
import unicodedata

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9, "两": 2,
}
_CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_BIG = {"万": 10_000, "萬": 10_000, "亿": 100_000_000, "億": 100_000_000}


def to_halfwidth(s: str) -> str:
    """全角字母/数字/标点 → 半角；保留中文标点里的括号为半角便于正则。"""
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def normalize_space(s: str) -> str:
    s = s.replace(" ", " ").replace("​", "")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


_DOUBLED_RE = re.compile(r"^((.)\2)+$")


def dedupe_doubled(s: str) -> str:
    """整串每个字都成对出现（加粗双写伪影）时去重；否则原样返回。

    仅当整串满足 ^((.)\\2)+$ 才处理，避免误伤"谢谢""哈哈"。
    """
    t = s.strip()
    if len(t) >= 4 and _DOUBLED_RE.match(t):
        return t[::2]
    # 单对叠字：只对数字/等级/否定等不会成词的字放行（"叁叁"→"叁"，"于于"→"于"），"谢谢"之类不动
    if len(t) == 2 and t[0] == t[1] and t[0] in _SAFE_SINGLE_DEDUPE:
        return t[0]
    return s


_SAFE_SINGLE_DEDUPE = set("壹贰叁肆伍陆柒捌玖零一二三四五六七八九十级于不得应须元万天日月年个项家人%0123456789")


def dedupe_doubled_in_line(line: str) -> str:
    """一行里按空格分词，对每个词做双写去重（标题行常见：'条条款款  名名称称'）。"""
    parts = line.split(" ")
    return " ".join(dedupe_doubled(p) for p in parts)


_DOUBLED_RUN_RE = re.compile(r"(?:(.)\1){3,}")


def dedupe_doubled_runs(s: str) -> str:
    """长文本里混合出现的加粗双写段（"提提交交电电汇汇或或银银行行"）：
    连续 ≥3 对"同字相邻"的片段折半。正常文本极少出现 3 对以上连续叠字，误伤可忽略。
    """

    def _half(m: re.Match) -> str:
        seg = m.group(0)
        return seg[::2]

    return _DOUBLED_RUN_RE.sub(_half, s)


def collapse_vertical(lines: list[str]) -> list[str]:
    """把连续的"单字行"折叠成一个词（竖排表格单元格）。

    连续 ≥3 行、每行恰好 1 个非空字符（汉字/字母/数字）时合并。
    """
    out: list[str] = []
    buf: list[str] = []

    def flush():
        if len(buf) >= 3:
            out.append("".join(buf))
        else:
            out.extend(buf)
        buf.clear()

    for ln in lines:
        t = ln.strip()
        if len(t) == 1 and (t.isalnum() or "一" <= t <= "鿿"):
            buf.append(t)
        else:
            flush()
            out.append(ln)
    flush()
    return out


def fix_kangxi(s: str) -> str:
    """政府采购网导出的 PDF 常把"一/文/人/方"等字编成康熙部首（U+2F00-2FDF）或 CJK 部首补充（U+2E80-2EFF），
    逐字 NFKC 还原为通用汉字；不对整串做 NFKC（会把 ①② 变成数字，破坏编号解析）。"""
    if not any(0x2E80 <= ord(ch) <= 0x2FDF for ch in s):
        return s
    return "".join(unicodedata.normalize("NFKC", ch) if 0x2E80 <= ord(ch) <= 0x2FDF else ch for ch in s)


def normalize_line(line: str) -> str:
    return normalize_space(to_halfwidth(fix_kangxi(line)))


def cn_to_int(s: str) -> int | None:
    """中文数字（含大写）→ 整数。支持 "一" "十二" "二十" "叁仟壹佰柒拾万捌仟柒佰叁拾玖"。

    非法输入返回 None。
    """
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total = 0      # 已完成的"万/亿"段
    section = 0    # 当前万以下段
    number = 0     # 当前位数字
    seen = False
    for ch in s:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
            seen = True
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if number == 0 and unit == 10 and section == 0:
                number = 1          # "十二" → 12
            section += number * unit
            number = 0
            seen = True
        elif ch in _CN_BIG:
            big = _CN_BIG[ch]
            section += number
            number = 0
            if big == 100_000_000:
                total = (total + section) * big
            else:
                total += section * big
            section = 0
            seen = True
        elif ch in "零〇":
            number = 0
        else:
            return None
    if not seen:
        return None
    return total + section + number


_CN_MONEY_RE = re.compile(r"^(?P<yuan>[零〇一二三四五六七八九壹贰叁肆伍陆柒捌玖两十拾百佰千仟万萬亿億]+)元?(?P<jiao>[零〇一二三四五六七八九壹贰叁肆伍陆柒捌玖]角)?(?P<fen>[零〇一二三四五六七八九壹贰叁肆伍陆柒捌玖]分)?整?$")


def cn_money_to_int(s: str) -> int | None:
    """中文大写金额 → 整数元（角分四舍五入）。"叁仟壹佰柒拾万捌仟柒佰叁拾玖元整" → 31708739。"""
    t = re.sub(r"[\s（）()¥￥人民币大写：:]", "", s)
    t = t.replace("圆", "元")
    m = _CN_MONEY_RE.match(t)
    if not m:
        return None
    yuan = cn_to_int(m.group("yuan"))
    if yuan is None:
        return None
    jiao = _CN_DIGITS.get((m.group("jiao") or " ")[0], 0)
    fen = _CN_DIGITS.get((m.group("fen") or " ")[0], 0)
    cents = jiao * 10 + fen
    return yuan + (1 if cents >= 50 else 0)


_NUM_RE = re.compile(r"(?P<num>\d[\d,，]*(?:\.\d+)?)\s*(?P<unit>万元|万|元|圆)?")


def money(s: str) -> int | None:
    """阿拉伯金额字符串 → 整数元。"34,652,556元" → 34652556；"34.65万元" → 346500；"1万" → 10000。"""
    t = to_halfwidth(s)
    m = _NUM_RE.search(t)
    if not m:
        return None
    raw = m.group("num").replace(",", "").replace("，", "")
    try:
        val = float(raw)
    except ValueError:
        return None
    unit = m.group("unit") or ""
    if unit.startswith("万"):
        val *= 10_000
    return int(round(val))


def first_int(s: str) -> int | None:
    m = re.search(r"\d+", to_halfwidth(s))
    return int(m.group(0)) if m else None


def strip_project_name(s: str) -> str:
    """项目名归一化：去空格/全半角/括号差异，便于全等比较。"""
    t = to_halfwidth(s)
    t = re.sub(r"\s+", "", t)
    t = t.replace("(", "（").replace(")", "）")
    return t


def similarity(a: str, b: str) -> float:
    """字符集 Jaccard 相似度（对短标题够用，无第三方依赖）。"""
    sa, sb = set(strip_project_name(a)), set(strip_project_name(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)
