"""FJ-X 交叉一致性：投标函 ↔ 封3 ↔ 表2 ↔ 附录 ↔ 人员表 ↔ 定标文件。"""
from __future__ import annotations

import re

from services.fujian_check.models import Finding, Location, Money
from services.fujian_check.rules._helpers import fmt_money, money_set, norm
from services.fujian_check.rules.base import Rule, RuleContext

GROUP = "交叉一致性"


class X01TotalPrice(Rule):
    id, group, title, severity = "FJ-X-01", GROUP, "投标总价：投标函大写/小写 ↔ 封3 ↔ 表2合计 一致", "reject"
    supersedes = ["pricing", "consistency"]
    source_clause = "5.1.5/5.1.7/6.1.1"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        L = ctx.idx.letter
        if L is None:
            return [self.manual("投标总价四处一致", "未找到投标函")]
        cover = ctx.idx.boq_cover
        cover_total = Money(value=int(cover["total"])) if cover.get("total", "").isdigit() else None
        summ = ctx.idx.boq_summary.get("合计")
        vals = {"投标函小写": L.total_price, "投标函大写": L.total_price_cn_value, "封3小写": cover_total, "表2合计": summ}
        present = {k: v for k, v in vals.items() if v is not None}
        ev: list[Location] = [L.loc] if L.loc else []
        if cover.get("page"):
            ev.append(ctx.bid_page_loc(int(cover["page"]), "封3 投标报价", "投标报价(小写)"))
        sp = ctx.idx.boq_summary.get("_page")
        if sp:
            ev.append(ctx.bid_page_loc(sp.value, "表2 工程项目造价汇总表", "合计"))
        actual = "；".join(f"{k} {fmt_money(v)}" for k, v in present.items())
        req_loc = ctx.clause_loc("5.1.7") or ctx.clause_loc("5.1.5")
        if len(present) < 2:
            return [self.manual("投标总价各处一致", actual or "未抽到总价", ev, req_loc)]
        if len(money_set(*present.values())) == 1:
            return [self.make("pass", requirement="投标函大写、小写、封3、表2合计四处金额必须一致（6.1.1 文字数额优先）",
                              requirement_loc=req_loc, actual=actual, evidence=ev)]
        return [self.make("fail", requirement="投标函大写、小写、封3、表2合计四处金额必须一致",
                          requirement_loc=req_loc, actual=actual, evidence=ev,
                          missing="各处总价不一致，评标按 6.1 算术修正，严重时视为多个报价（5.1.5）",
                          fix="统一为同一金额，修改后重新导出 XML 与 PDF")]


class X02Appendix(Rule):
    id, group, title, severity = "FJ-X-02", GROUP, "投标函附录 7 项合同参数 ↔ 招标文件附录", "reject"
    supersedes = ["compliance", "consistency"]
    source_clause = "6.2.2"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        tender_ap = ctx.req.appendix_params
        bid_ap = ctx.idx.appendix
        if not tender_ap:
            return [self.na("招标文件未解析出投标函附录")]
        if not bid_ap:
            fr = ctx.form("投标函附录")
            return [self.make("fail", requirement="投标函附录须按招标格式逐项填写", actual="投标文件未解析出附录表",
                              evidence=[ctx.form_loc(fr)] if fr else [], missing="附录缺失或为图片",
                              fix="按第8章格式补齐投标函附录")]
        for key, tv in tender_ap.items():
            bv = bid_ap.get(key)
            if bv is None:
                out.append(self.make("fail", requirement=f"{key}：{tv.value}", requirement_loc=tv.loc,
                                     actual="投标附录无此项", missing=f"附录缺少「{key}」", fix="补填该行"))
                continue
            same = norm(str(tv.value)) == norm(str(bv.value))
            if not same:
                nums_t = re.findall(r"\d+(?:\.\d+)?", str(tv.value))
                nums_b = re.findall(r"\d+(?:\.\d+)?", str(bv.value))
                same = bool(nums_t) and nums_t == nums_b
            out.append(self.make("pass" if same else "fail", requirement=f"{key}：{tv.value}", requirement_loc=tv.loc,
                                 actual=str(bv.value), evidence=[bv.loc] if bv.loc else [],
                                 missing="" if same else "与招标附录约定不一致（6.2.2：与投标函矛盾时以投标函为准，但偏离招标要求可能被否决）",
                                 fix="" if same else "改回招标文件附录原文"))
        return out


class X03ProjectManager(Rule):
    id, group, title, severity = "FJ-X-03", GROUP, "项目负责人姓名：投标函 ↔ 人员表 ↔ 简要情况表 ↔ 定标文件", "major"
    supersedes = ["consistency"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        names: dict[str, Location | None] = {}
        L = ctx.idx.letter
        if L and L.pm_name:
            names[f"投标函:{L.pm_name}"] = L.loc
        if ctx.idx.personnel:
            for p in ctx.idx.personnel.by_post("项目负责人"):
                names[f"人员表:{p.name}"] = ctx.bid_page_loc(p.page, "拟派出施工现场管理人员表") if p.page else None
        award_fr = next((f for f in ctx.idx.forms if f.form and f.form.section == "定标文件"
                         and "项目负责人相关信息" in f.form.title and f.page_start), None)
        if award_fr:
            t = ctx.bdoc.page_text(award_fr.page_start)
            m = re.search(r"姓\s*名\s*([一-龥]{2,4})", t)
            if m:
                names[f"定标文件:{m.group(1)}"] = ctx.form_loc(award_fr)
        distinct = {k.split(":", 1)[1] for k in names}
        ev = [v for v in names.values() if v]
        actual = "；".join(names.keys())
        if not names:
            return [self.manual("项目负责人姓名各处一致", "未抽到项目负责人姓名")]
        if len(distinct) == 1:
            return [self.make("pass", requirement="项目负责人在投标函、人员表、简要情况表、定标文件中必须为同一人",
                              actual=actual, evidence=ev)]
        return [self.make("fail", requirement="项目负责人各处必须为同一人", actual=actual, evidence=ev,
                          missing="项目负责人姓名不一致", fix="核对并统一为拟派出的同一人")]


class X05BidderName(Rule):
    id, group, title, severity = "FJ-X-05", GROUP, "投标人名称在各封面/签章处一致", "major"
    supersedes = ["consistency"]
    source_clause = "3.1.10"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        rx = re.compile(r"投\s*标\s*人\s*(?:名称)?\s*[:：]\s*([一-龥（）()]{4,40}?(?:公司|集团|局|院|所))")
        seen: dict[str, Location] = {}
        tech = ctx.idx.sections.get("技术文件")
        for p in range(1, ctx.bdoc.n_pages + 1):
            if tech and tech[0] <= p <= tech[1]:
                continue
            for m in rx.finditer(ctx.bdoc.page_text(p)):
                nm = norm(m.group(1))
                if nm not in seen:
                    seen[nm] = ctx.bid_page_loc(p, None, m.group(0))
        req_loc = ctx.clause_loc("3.1.10")
        if not seen:
            return [self.manual("投标人名称一致", "未匹配到投标人签署行", [], req_loc)]
        if len(seen) == 1:
            return [self.make("pass", requirement="投标文件各处投标人名称须与营业执照一致", requirement_loc=req_loc,
                              actual=next(iter(seen)), evidence=list(seen.values())[:2])]
        return [self.make("warning", requirement="投标文件各处投标人名称须一致", requirement_loc=req_loc,
                          actual="；".join(seen.keys()), evidence=list(seen.values())[:4],
                          missing="出现多个投标人名称写法", fix="统一为营业执照全称")]


class X06SafetyFee(Rule):
    id, group, title, severity = "FJ-X-06", GROUP, "表2 安全文明施工费与暂列金额已单列", "minor"
    method = "table"
    supersedes = ["pricing"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        s = ctx.idx.boq_summary
        sp = s.get("_page")
        ev = [ctx.bid_page_loc(sp.value, "表2 工程项目造价汇总表")] if sp else []
        vals = {k: v.value for k, v in s.items() if not k.startswith("_")}
        if not vals:
            return [self.manual("表2 汇总表金额结构", "未解析出表2")]
        ok = "合计_安全文明施工费" in vals and "暂列金额" in vals
        return [self.make("pass" if ok else "warning",
                          requirement="工程项目造价汇总表应列明安全文明施工费与暂列金额（7.1.3/7.1.5 详细评审依据）",
                          actual="；".join(f"{k} {v:,}" for k, v in vals.items()), evidence=ev,
                          missing="" if ok else "汇总表未单列安全文明施工费或暂列金额")]


RULES = [X01TotalPrice, X02Appendix, X03ProjectManager, X05BidderName, X06SafetyFee]
