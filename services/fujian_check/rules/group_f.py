"""FJ-F 第8章表单齐全性与顺序。"""
from __future__ import annotations

from services.fujian_check.models import Finding, FormRange
from services.fujian_check.rules._helpers import form_status
from services.fujian_check.rules.base import Rule, RuleContext
from services.fujian_check.textnorm import cn_to_int

GROUP = "表单齐全性"


def _section_findings(rule: Rule, ctx: RuleContext, section: str, clause: str | None) -> list[Finding]:
    req_forms = ctx.req.forms_in(section)  # type: ignore[arg-type]
    if not req_forms:
        return [rule.na(f"招标第8章未解析出{section}表单清单")]
    frs = [f for f in ctx.idx.forms if f.form and f.form.section == section]
    req_loc = ctx.clause_loc(clause) if clause else None
    out: list[Finding] = []
    located = 0
    for fr in sorted(frs, key=lambda f: cn_to_int(f.form.no) or 0):
        f = fr.form
        label = f"{f.no}、{f.title}"
        if fr.match_kind in ("exact", "alias", "fuzzy"):
            located += 1
            continue
        if fr.match_kind == "scanned":
            out.append(rule.make("warning", requirement=f"须提交「{label}」", requirement_loc=f.loc or req_loc,
                                 actual=f"文字层未见标题，p{fr.page_start}-{fr.page_end} 为扫描页，疑似以扫描件提交",
                                 evidence=[ctx.bid_page_loc(q, label) for q in fr.scanned_pages[:3]],
                                 missing="需 OCR/人工确认扫描件即为该表单且已签章", fix="如确为该表单，建议同时保留文字版",
                                 method="manual", confidence=0.6))
            continue
        if f.optional:
            out.append(rule.make("na", requirement=f"「{label}」为可选（如有时）", requirement_loc=f.loc, actual="未提交"))
            continue
        out.append(rule.make("fail", requirement=f"须提交「{label}」", requirement_loc=f.loc or req_loc, actual="未找到",
                             missing=f"缺少{section}表单「{label}」（{clause or '第8章'}）", fix="按第8章格式补齐并签章"))
    summary_verdict = "fail" if any(x.verdict == "fail" for x in out) else ("warning" if any(x.verdict == "warning" for x in out) else "pass")
    out.insert(0, rule.make(summary_verdict, requirement=f"{section}共 {len(req_forms)} 项表单齐全", requirement_loc=req_loc,
                            actual=f"已定位 {located} 项；" + "；".join(f"{fr.form.no} {form_status(fr)}" for fr in frs if fr.match_kind not in ('exact','alias','fuzzy'))[:400],
                            missing="" if summary_verdict == "pass" else "存在缺失或未确认的表单"))
    return out


class F01Qualification(Rule):
    id, group, title, severity = "FJ-F-01", GROUP, "资格文件表单齐全（第8章第1节）", "reject"
    supersedes = ["docIntegrity"]
    source_clause = "3.1.1"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        return _section_findings(self, ctx, "资格文件", "3.1.1")


class F02Commercial(Rule):
    id, group, title, severity = "FJ-F-02", GROUP, "商务文件表单齐全（第8章第2节）", "reject"
    supersedes = ["docIntegrity"]
    source_clause = "5.1.1"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        return _section_findings(self, ctx, "商务文件", "5.1.1")


class F04Award(Rule):
    id, group, title, severity = "FJ-F-04", GROUP, "定标文件表单齐全（第8章附件2）", "major"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        if not ctx.req.profile.variant.get("评定分离"):
            return [self.na("非评定分离项目")]
        return _section_findings(self, ctx, "定标文件", None)


class F05Order(Rule):
    id, group, title, severity = "FJ-F-05", GROUP, "表单编排顺序与第8章一致", "minor"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        bad: list[str] = []
        for section in ("资格文件", "商务文件", "定标文件"):
            frs = [f for f in ctx.idx.forms if f.form and f.form.section == section and f.page_start and f.match_kind != "scanned"]
            frs.sort(key=lambda f: cn_to_int(f.form.no) or 0)
            pages = [f.page_start for f in frs]
            if pages != sorted(pages):
                bad.append(section)
        if bad:
            return [self.make("warning", requirement="投标文件表单顺序应与第8章目录一致", actual=f"{'、'.join(bad)} 顺序与目录不一致",
                              missing="顺序错乱可能被视为格式不符（3.1.1/5.1.1）", fix="按目录顺序重排")]
        return [self.make("pass", requirement="投标文件表单顺序应与第8章目录一致", actual="各节表单页码单调递增")]


RULES = [F01Qualification, F02Commercial, F04Award, F05Order]
