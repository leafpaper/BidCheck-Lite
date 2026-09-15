"""FJ-C 扫描件证书：OCR 读证书要素，核对有效期覆盖投标截止日、单位名称与投标人一致、等级达标。

未启用 OCR 时只定位候选页并标 manual。
"""
from __future__ import annotations

import re

from services.fujian_check.models import Finding, FormRange
from services.fujian_check.rules._helpers import level_rank, norm
from services.fujian_check.rules.base import Rule, RuleContext
from services.fujian_check.rules.ocr import dates_in, is_unreadable, lines_of_type

GROUP = "扫描件证书"

# (id, 标题, 定位表单关键字, 招标条款, OCR 行关键词, 是否核对单位名称)
_CERTS = [
    ("FJ-C-01", "营业执照有效且单位名称=投标人", "投标人基本情况表", "3.1.4", ("营业执照", "统一社会信用代码"), True),
    ("FJ-C-02", "建筑业企业资质证书等级达标且有效", "投标人基本情况表", "3.1.5", ("资质证书", "资质等级", "总承包"), True),
    ("FJ-C-03", "安全生产许可证有效", "投标人基本情况表", "3.1.5", ("安全生产许可证", "安许"), True),
    ("FJ-C-04", "项目负责人建造师注册证书与安B证有效、注册单位=投标人", "项目负责人简要情况表", "3.1.8", ("建造师", "注册证书", "安全生产考核", "B证"), True),
    ("FJ-C-05", "安全员 C 证有效且附社保凭证", "安全员及其社保凭证", "3.1.8", ("安全生产考核", "C证", "社保", "社会保险"), False),
    ("FJ-C-06", "保证金到账/保函日期不晚于投标截止时间", "投标保证金", "3.1.3", ("电汇", "转账", "保函", "回单", "凭证"), False),
]


def _iso(s: str) -> str:
    y, m, d = re.split(r"[.\-/]", s)[:3]
    return f"{y}-{int(m):02d}-{int(d):02d}"


def _validity_ends(line: str) -> list[str]:
    """OCR 行 '类型|名称|编号|机关|有效期(起-止)|持有人|其他' → 明确的止日期或'长期'；起止不清/仅一个日期不作判定。"""
    fields = [f.strip() for f in line.split("|")]
    seg = fields[4] if len(fields) >= 5 else ""
    if not seg:
        return []
    if re.search(r"长期|永久", seg):
        return ["长期"]
    if "不清" in seg:
        return []
    ds = dates_in(seg)
    if len(ds) >= 2:
        return [max(ds)]
    return []


def _deadline_date(s: str | None) -> str | None:
    if not s:
        return None
    ds = dates_in(s)
    return ds[0] if ds else None


class CertRule(Rule):
    group = GROUP
    severity = "reject"
    method = "ocr"
    supersedes = ["qualification", "validity"]

    def __init__(self, rid: str, title: str, form_key: str, clause: str, kws: tuple[str, ...], check_unit: bool):
        self.id, self.title, self.form_key, self.source_clause, self.kws, self.check_unit = rid, title, form_key, clause, kws, check_unit

    def _pages(self, ctx: RuleContext) -> tuple[FormRange | None, list[int]]:
        fr = ctx.form(self.form_key)
        if not fr or not fr.page_start:
            return None, []
        pages = fr.scanned_pages or list(range(fr.page_start, (fr.page_end or fr.page_start) + 1))
        return fr, pages[:8]

    def ocr_pages_needed(self, ctx: RuleContext) -> list[int]:
        return self._pages(ctx)[1]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        fr, pages = self._pages(ctx)
        req_loc = ctx.clause_loc(self.source_clause)
        deadline = _deadline_date(ctx.req.hard.deadline)
        if not fr:
            return [self.make("warning", requirement=self.title, requirement_loc=req_loc, actual="未定位到对应表单",
                              missing="无法定位证书扫描件", fix="确认表单已提交", method="manual", confidence=0.5)]
        title = fr.form.title if fr.form else fr.matched_title
        ev = [ctx.bid_page_loc(p, title) for p in pages[:4]]
        texts = {p: ctx.ocr_text.get(p, "") for p in pages if ctx.ocr_text.get(p)}
        if not texts:
            return [self.manual(f"{self.title}（截止 {deadline or '投标截止日'}）", f"证书为扫描件 p{pages[0]}-{pages[-1]}，未启用 OCR",
                                ev, req_loc, fix="开启视觉 OCR 或人工翻页核对")]
        joined = "\n".join(texts.values())
        if all(is_unreadable(t) for t in texts.values()):
            return [self.make("warning", requirement=self.title, requirement_loc=req_loc, actual="OCR 无法辨认扫描件",
                              evidence=ev, missing="字迹模糊可能触发 3.1.2 否决", fix="重新扫描清晰件", confidence=0.6)]
        rel = lines_of_type(joined, *self.kws)
        # 文字层兜底：表单页本身（如项目负责人简要情况表）常已写明证书号与"使用有效期"
        layer = "\n".join(ctx.bdoc.page_text(p) for p in range(fr.page_start, (fr.page_end or fr.page_start) + 1))
        layer_lines = [ln for ln in layer.split("\n") if any(k in ln for k in self.kws) or "有效期" in ln]
        if not rel and not layer_lines:
            return [self.manual(self.title, f"OCR 未识别到相关证书行；OCR 摘要：{joined[:200]}", ev, req_loc)]
        problems: list[str] = []
        # 有效期：OCR 结构化行第 5 列（起-止）；只有明确的"止"日期才用于判定
        ends: list[str] = []
        for ln in rel:
            ends.extend(_validity_ends(ln))
        for m in re.finditer(r"有效期[^\d]{0,20}(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})\s*[-~—至到]\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})", layer):
            ends.append(_iso(m.group(2)))
        if deadline and ends:
            bad = [e for e in ends if e != "长期" and e < deadline]
            if bad:
                problems.append(f"有效期止 {min(bad)} 早于投标截止 {deadline}")
        if not rel:
            rel = layer_lines
        # 单位名称
        if self.check_unit and ctx.idx.bidder_name:
            units = [ln for ln in rel if norm(ctx.idx.bidder_name) in norm(ln)]
            if not units and any(re.search(r"公司|集团", ln) for ln in rel):
                problems.append("证书上的单位名称未见与投标人一致")
        # 资质等级（C-02）
        if self.id == "FJ-C-02":
            need = level_rank(ctx.req.hard.qualification)
            got = [level_rank(ln) for ln in rel]
            got = [g for g in got if g is not None]
            if need is not None and got and min(got) > need:
                problems.append("资质等级低于招标要求")
        # 截止日期（C-06）
        if self.id == "FJ-C-06" and deadline:
            ds = [d for d in dates_in(joined)]
            late = [d for d in ds if d > deadline]
            if ds and late and not [d for d in ds if d <= deadline]:
                problems.append(f"凭证日期 {late[0]} 晚于投标截止")
        actual = "OCR：" + "；".join(r[:80] for r in rel[:4])
        if problems:
            return [self.make("fail", requirement=f"{self.title}（截止 {deadline or '—'}）", requirement_loc=req_loc, actual=actual,
                              evidence=ev, missing="；".join(problems), fix="更换/续期证书或核对单位名称", confidence=0.7)]
        conf_note = "" if ends else "（未识别到有效期，需人工复核）"
        return [self.make("pass" if ends else "warning", requirement=f"{self.title}（截止 {deadline or '—'}）", requirement_loc=req_loc,
                          actual=actual + conf_note, evidence=ev, missing="" if ends else "OCR 未读出有效期", confidence=0.75)]


RULES = [CertRule(*c) for c in _CERTS]
