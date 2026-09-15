"""投标函 / 投标报价封面（封3）/ 工程项目造价汇总表（表2）取值。"""
from __future__ import annotations

import re

from services.fujian_check.models import BidLetter, Location, Money, ParsedDoc
from services.fujian_check.tender._tables import tables_for_pages
from services.fujian_check.textnorm import cn_money_to_int, money, to_halfwidth


def _flat(text: str) -> str:
    """去掉所有空白（中文无需空格，跨行断字直接拼接）。"""
    return re.sub(r"\s+", "", to_halfwidth(text))


_TOTAL_RE = re.compile(r"人民币\(大写\)(?P<cn>[零〇一二三四五六七八九壹贰叁肆伍陆柒捌玖两十拾百佰千仟万萬亿億元圆整角分]+?)元?整?元?\(?[¥￥]\s*(?P<num>[\d,\.]+)\s*元")
_TOTAL_NUM_RE = re.compile(r"[¥￥]\s*(?P<num>[\d,\.]+)\s*元\)?的?投标总报价")
_DUR_RE = re.compile(r"工期(?P<d>\d{2,4})日历天")
_DUR2_RE = re.compile(r"总工期为?(?P<d>\d{2,4})日历天")
_QUOTA_RE = re.compile(r"定额工期(?P<d>\d{2,4})")
_QUALITY_RE = re.compile(r"工程质量(?:达到|为|标准[:：]?)(?P<q>.{4,60}?)(?:标准[,，。]|[,，。])")
_VALID_RE = re.compile(r"投标有效期(?:为|:|：)?(?P<d>\d{2,3})(?:日历天|天|日)")
_DEPOSIT_RE = re.compile(r"投标保证金[^。]{0,30}?(?:人民币\(大写\)(?P<cn>[零〇一二三四五六七八九壹贰叁肆伍陆柒捌玖两十拾百佰千仟万萬亿億元圆整]+?))?\(?[¥￥]\s*(?P<num>[\d,\.]+)\s*元")
_PM_RE = re.compile(r"派出(?P<name>[一-龥]{2,4}?)(?P<cert>(?:闽|[A-Z])?[\dA-Za-z]{8,20})为本项目的项目负责人")
_PM2_RE = re.compile(r"项目负责人(?:姓名)?[:：]?(?P<name>[一-龥]{2,4})")
_DATE_RE = re.compile(r"(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})日?")
_BIDDER_RE = re.compile(r"投标人[:：]?(?P<b>[一-龥（）()]{4,40}?(?:公司|集团|局|院|所))\(?盖单位公章")


def parse_bid_letter(doc: ParsedDoc, p0: int, p1: int) -> BidLetter:
    raw = "\n".join(doc.page_text(p) for p in range(p0, p1 + 1))
    t = _flat(raw)
    letter = BidLetter(loc=Location(doc="bid", page=p0, page_end=p1, section="第2节 商务文件", form="一、投标函",
                                    excerpt=raw[:300]))
    m = _TOTAL_RE.search(t)
    if m:
        letter.total_price_cn = m.group("cn")
        v = cn_money_to_int(m.group("cn"))
        if v is not None:
            letter.total_price_cn_value = Money(value=v, raw=m.group("cn"))
        n = money(m.group("num") + "元")
        if n is not None:
            letter.total_price = Money(value=n, raw=m.group("num"))
    else:
        m2 = _TOTAL_NUM_RE.search(t)
        if m2:
            n = money(m2.group("num") + "元")
            if n is not None:
                letter.total_price = Money(value=n, raw=m2.group("num"))
    m = _DUR_RE.search(t) or _DUR2_RE.search(t)
    if m:
        letter.duration_days = int(m.group("d"))
    m = _QUOTA_RE.search(t)
    if m:
        letter.quota_duration_days = int(m.group("d"))
    m = _QUALITY_RE.search(t)
    if m:
        letter.quality = m.group("q")
    m = _VALID_RE.search(t)
    if m:
        letter.validity_days = int(m.group("d"))
    m = _DEPOSIT_RE.search(t)
    if m:
        n = money(m.group("num") + "元")
        if n is not None:
            letter.deposit = Money(value=n, raw=m.group("num"))
        if m.group("cn"):
            letter.deposit_cn = m.group("cn").rstrip("元圆整")
    m = _PM_RE.search(t)
    if m:
        letter.pm_name = m.group("name")
        letter.pm_cert_no = m.group("cert")
    else:
        m = _PM2_RE.search(t)
        if m:
            letter.pm_name = m.group("name")
    m = _BIDDER_RE.search(t)
    if m:
        letter.bidder = m.group("b")
    dates = _DATE_RE.findall(raw)
    if dates:
        y, mo, d = dates[-1]
        letter.date = f"{y}-{int(mo):02d}-{int(d):02d}"
    return letter


_COVER_TOTAL_RE = re.compile(r"投标报价\(小写\)[:：]?(?P<num>[\d,\.]+)元")
_COVER_CN_RE = re.compile(r"\(大写\)[:：]?(?P<cn>[零〇一二三四五六七八九壹贰叁肆伍陆柒捌玖两十拾百佰千仟万萬亿億]+?)[元圆]整?")


def parse_boq_cover(doc: ParsedDoc, rng: tuple[int, int] | None) -> dict[str, str]:
    """封3 投标报价：小写/大写总价。"""
    if not rng:
        return {}
    for p in range(rng[0], min(rng[0] + 3, rng[1]) + 1):
        t = _flat(doc.page_text(p))
        if "投标报价" in t and "小写" in t:
            out: dict[str, str] = {"page": str(p)}
            m = _COVER_TOTAL_RE.search(t)
            if m:
                out["total"] = m.group("num")
            # 大写金额在封面表格里被"其中:甲供材料费"等单元格切碎，去噪后取最长的大写数字串
            t2 = re.sub(r"\(大写\)[:：]?|人民币", "", t)
            t2 = re.sub(r"其中[:：]?甲供材料费", "", t2)
            runs = re.findall(r"[零〇一二三四五六七八九壹贰叁肆伍陆柒捌玖两十拾百佰千仟万萬亿億]{2,}", t2)
            if runs:
                joined = "".join(runs)
                total = money((out.get("total") or "0") + "元")
                # 被表格单元格切碎的大写串：拼接后能与小写对上就用拼接结果，否则取最长片段
                if total and cn_money_to_int(joined) == total:
                    out["total_cn"] = joined
                else:
                    out["total_cn"] = max(runs, key=len)
            return out
    return {}


def parse_boq_summary(doc: ParsedDoc, rng: tuple[int, int] | None) -> dict[str, Money]:
    """表2 工程项目造价汇总表：合计 / 安全文明施工费 / 人工费 / 暂列金额。"""
    if not rng:
        return {}
    out: dict[str, Money] = {}
    for p in range(rng[0], min(rng[0] + 6, rng[1]) + 1):
        head = doc.page_text(p)[:60]
        if "表2" not in head or "造价汇总" not in doc.page_text(p)[:120]:
            continue
        tables = tables_for_pages(doc, [p]).get(p, [])
        for tb in tables:
            header_idx: dict[str, int] = {}
            for row in tb:
                cells = [c.replace(" ", "") for c in row]
                is_header = not any(re.search(r"\d{3,}", c) for c in cells)
                if is_header:
                    # 表头可能占两行（"金额(元) | 其中" / "| 安全文明施工费(元) | 人工费(元)"）
                    for i, c in enumerate(cells):
                        if "安全文明" in c:
                            header_idx["安全文明施工费"] = i
                        elif "人工费" in c:
                            header_idx["人工费"] = i
                        elif "金额" in c and "金额" not in header_idx:
                            header_idx["金额"] = i
                    continue
                if not header_idx:
                    continue
                name = next((c for c in cells[:2] if c and not c.isdigit()), "")
                if name in ("合计", "总计"):
                    for k, i in header_idx.items():
                        if i < len(cells) and cells[i]:
                            v = money(cells[i] + "元")
                            if v is not None:
                                out[f"合计_{k}" if k != "金额" else "合计"] = Money(value=v, raw=cells[i])
                elif "暂列" in name:
                    i = header_idx.get("金额", 2)
                    if i < len(cells) and cells[i]:
                        v = money(cells[i] + "元")
                        if v is not None:
                            out["暂列金额"] = Money(value=v, raw=cells[i])
            if out:
                out["_page"] = Money(value=p, raw=str(p))
                return out
    return out
