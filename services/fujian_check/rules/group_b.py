"""FJ-B 商务文件初审否决（5.1.x）——确定性部分。"""
from __future__ import annotations

import re

from services.fujian_check.models import Finding, Money
from services.fujian_check.rules._helpers import fmt_money, money_set, norm
from services.fujian_check.rules.base import Rule, RuleContext

GROUP = "商务初审否决"


class B02CommercialVsQualification(Rule):
    id, group, title, severity = "FJ-B-02", GROUP, "5.1.2 商务文件与资格文件填报一致（投标人/项目负责人）", "reject"
    supersedes = ["consistency"]
    source_clause = "5.1.2"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        L = ctx.idx.letter
        req_loc = ctx.clause_loc("5.1.2")
        if L is None:
            return [self.manual("商务与资格一致", "未找到投标函", [], req_loc)]
        issues = []
        if L.bidder and ctx.idx.bidder_name and norm(L.bidder) != norm(ctx.idx.bidder_name):
            issues.append(f"投标人：投标函 {L.bidder} / 资格封面 {ctx.idx.bidder_name}")
        pm = next((p for p in (ctx.idx.personnel.people if ctx.idx.personnel else []) if p.post == "项目负责人"), None)
        if L.pm_name and pm and L.pm_name != pm.name:
            issues.append(f"项目负责人：投标函 {L.pm_name} / 人员表 {pm.name}")
        if L.pm_cert_no and pm and pm.cert_no and re.sub(r"\s", "", L.pm_cert_no) != re.sub(r"\s", "", pm.cert_no):
            issues.append(f"建造师注册号：投标函 {L.pm_cert_no} / 简要情况表 {pm.cert_no}")
        ev = [L.loc] if L.loc else []
        if issues:
            return [self.make("fail", requirement="商务文件相关内容须与资格文件填报一致", requirement_loc=req_loc,
                              actual="；".join(issues), evidence=ev, missing="商务与资格填报不一致（否决）", fix="统一后重新导出")]
        return [self.make("pass", requirement="商务文件相关内容须与资格文件填报一致", requirement_loc=req_loc,
                          actual=f"投标人 {L.bidder or ctx.idx.bidder_name}；项目负责人 {L.pm_name}", evidence=ev)]


class B05SinglePrice(Rule):
    id, group, title, severity = "FJ-B-05", GROUP, "5.1.5 同一项目只有一个报价", "reject"
    supersedes = ["pricing"]
    source_clause = "5.1.5"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        L = ctx.idx.letter
        req_loc = ctx.clause_loc("5.1.5")
        cover = ctx.idx.boq_cover
        vals = [L.total_price if L else None, L.total_price_cn_value if L else None,
                Money(value=int(cover["total"])) if cover.get("total", "").isdigit() else None, ctx.idx.boq_summary.get("合计")]
        s = money_set(*vals)
        if not s:
            return [self.manual("单一报价", "未抽到任何总价", [], req_loc)]
        ok = len(s) == 1
        return [self.make("pass" if ok else "fail", requirement="不得递交两份或多份内容不同的投标文件或多个报价", requirement_loc=req_loc,
                          actual="出现报价：" + "、".join(f"{v:,}" for v in sorted(s)), evidence=[L.loc] if L and L.loc else [],
                          missing="" if ok else "文件内出现多个不同总价", fix="" if ok else "统一总价")]


class B06NotAboveControlPrice(Rule):
    id, group, title, severity = "FJ-B-06", GROUP, "5.1.6 投标总价不高于招标控制价", "reject"
    supersedes = ["pricing"]
    source_clause = "5.1.6"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        cp = ctx.req.hard.control_price
        L = ctx.idx.letter
        req_loc = ctx.req.hard.sources.get("control_price") or ctx.clause_loc("5.1.6")
        if cp is None:
            return [self.manual("总价 ≤ 控制价", "招标未抽到控制价", [], req_loc)]
        total = (L.total_price if L else None) or ctx.idx.boq_summary.get("合计")
        if total is None:
            return [self.manual(f"总价 ≤ 控制价 {fmt_money(cp)}", "未抽到投标总价", [], req_loc)]
        ok = total.value <= cp.value
        ratio = total.value / cp.value * 100 if cp.value else 0
        return [self.make("pass" if ok else "fail", requirement=f"投标总价不高于招标控制价 {fmt_money(cp)}", requirement_loc=req_loc,
                          actual=f"投标总价 {fmt_money(total)}（控制价的 {ratio:.1f}%）", evidence=[L.loc] if L and L.loc else [],
                          missing="" if ok else "总价高于控制价（否决）", fix="" if ok else "下调报价")]


class B08DurationQuality(Rule):
    id, group, title, severity = "FJ-B-08", GROUP, "5.1.8 投标函工期不超要求、质量不低于要求", "reject"
    supersedes = ["compliance"]
    source_clause = "5.1.8"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h, L = ctx.req.hard, ctx.idx.letter
        req_loc = ctx.clause_loc("5.1.8")
        if L is None:
            return [self.manual("工期与质量承诺", "未找到投标函", [], req_loc)]
        problems = []
        if h.duration_days and L.duration_days and L.duration_days > h.duration_days:
            problems.append(f"工期 {L.duration_days} > {h.duration_days}")
        if h.quality and L.quality and "合格" in h.quality and not re.search(r"合格|优良", L.quality):
            problems.append("质量承诺低于合格标准")
        if not problems and (L.duration_days is None or not L.quality):
            return [self.manual("投标函工期与质量", f"工期 {L.duration_days}；质量 {L.quality}", [L.loc] if L.loc else [], req_loc)]
        ok = not problems
        return [self.make("pass" if ok else "fail", requirement="投标函载明的工期不超过、质量不低于招标文件要求", requirement_loc=req_loc,
                          actual="；".join(problems) if problems else f"工期 {L.duration_days} 日历天；质量 {L.quality}",
                          evidence=[L.loc] if L.loc else [], missing="" if ok else "否决项", fix="" if ok else "按招标要求修改投标函")]


class B04XmlDoc(Rule):
    id, group, title, severity = "FJ-B-04", GROUP, "5.1.4 已提交已标价工程量清单电子文档（XML）", "reject"
    method = "manual"
    supersedes = ["ebidSubmit"]
    source_clause = "5.1.4"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        req_loc = ctx.clause_loc("5.1.4")
        if not ctx.req.hard.xml_required:
            return [self.na("招标未要求 XML 电子清单", req_loc)]
        fr = ctx.form("编制人员情况表")
        boq = ctx.idx.boq_page_range
        ev = [x for x in ([ctx.form_loc(fr)] if fr and fr.page_start else []) + ([ctx.bid_page_loc(boq[0], "已标价工程量清单")] if boq else []) if x]
        return [self.manual("须随投标文件提交符合造价电子数据交换导则的 XML 清单（20.1/20.7）",
                            f"PDF 内已标价清单 {'p%d-%d' % boq if boq else '未识别'}；XML 文件需在平台上传处自查",
                            ev, req_loc, fix="在电子标书生成器中确认 XML 已随附且与 PDF 一致")]


RULES = [B02CommercialVsQualification, B04XmlDoc, B05SinglePrice, B06NotAboveControlPrice, B08DurationQuality]
