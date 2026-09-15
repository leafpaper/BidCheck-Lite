"""FJ-D 商务详细评审否决（7.1.x）。v1 只做汇总层可核对项，单价 85% 线需招标控制价清单（v2）。"""
from __future__ import annotations

import re

from services.fujian_check.models import Finding
from services.fujian_check.rules._helpers import fmt_money
from services.fujian_check.rules.base import Rule, RuleContext
from services.fujian_check.textnorm import money, to_halfwidth

GROUP = "详细评审否决"


class D03SafetyFee(Rule):
    id, group, title, severity = "FJ-D-03", GROUP, "7.1.3 安全文明施工费不低于招标控制价相应金额/费率", "reject"
    method = "manual"
    supersedes = ["pricing"]
    source_clause = "7.1.3"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        v = ctx.idx.boq_summary.get("合计_安全文明施工费")
        sp = ctx.idx.boq_summary.get("_page")
        ev = [ctx.bid_page_loc(sp.value, "表2 工程项目造价汇总表")] if sp else []
        return [self.manual("安全文明施工费不得低于招标控制价相应金额（按费率计的不低于相应费率）",
                            f"投标汇总表安全文明施工费 {fmt_money(v)}；招标控制价对应金额需查招标清单/控制价文件", ev, ctx.clause_loc("7.1.3"),
                            fix="与招标控制价清单中的安全文明施工费逐项比对")]


class D05ProvisionalSums(Rule):
    id, group, title, severity = "FJ-D-05", GROUP, "7.1.5 暂列金额/专业工程暂估价/甲供材按招标清单金额填写", "reject"
    supersedes = ["pricing"]
    source_clause = "7.1.5"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        bid_v = ctx.idx.boq_summary.get("暂列金额")
        sp = ctx.idx.boq_summary.get("_page")
        ev = [ctx.bid_page_loc(sp.value, "表2 工程项目造价汇总表")] if sp else []
        req_loc = ctx.clause_loc("7.1.5")
        # 招标文件里找"暂列金额 xxx 元"
        want = None
        want_loc = None
        for s in ctx.req.sections:
            if s.chapter in (1, 2, 3, 4, 5):
                for p in range(s.page_start, min(s.page_end, s.page_start + 40) + 1):
                    t = to_halfwidth(ctx.tdoc.page_text(p))
                    m = re.search(r"暂列金额[^\d。]{0,20}?(\d[\d,]*(?:\.\d+)?)\s*(万元|万|元)", t)
                    if m:
                        want = money(m.group(1) + m.group(2))
                        from services.fujian_check.models import Location

                        want_loc = Location(doc="tender", page=p, section=f"第{s.chapter}章", excerpt=m.group(0))
                        break
                if want:
                    break
        if bid_v is None:
            return [self.manual("暂列金额等按招标清单金额填写", "投标汇总表未单列暂列金额", ev, req_loc)]
        if want is None:
            return [self.manual("暂列金额等按招标清单金额填写", f"投标暂列金额 {fmt_money(bid_v)}；招标清单金额未能机器抽取", ev, req_loc,
                                fix="与招标工程量清单暂列金额比对")]
        ok = want == bid_v.value
        return [self.make("pass" if ok else "fail", requirement=f"暂列金额 {want:,} 元（按招标清单）", requirement_loc=want_loc or req_loc,
                          actual=f"投标暂列金额 {fmt_money(bid_v)}", evidence=ev, missing="" if ok else "暂列金额与招标清单不一致（否决）",
                          fix="" if ok else "改为招标清单金额")]


class D01D02D04D06Manual(Rule):
    id, group, title, severity = "FJ-D-01", GROUP, "7.1.1/7.1.2/7.1.4/7.1.6 综合单价、措施费、主材单价 85% 线与清单五要素一致", "reject"
    method = "manual"
    supersedes = ["pricingLogic"]
    source_clause = "7.1.1"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        boq = ctx.idx.boq_page_range
        ev = [ctx.bid_page_loc(boq[0], "已标价工程量清单")] if boq else []
        rows = [d for d in ctx.req.datasheet if d.clause.startswith("7.1")]
        req_loc = rows[0].loc if rows else ctx.clause_loc("7.1.1")
        return [self.manual("主要分部分项综合单价、措施项目费、主要材料设备单价不得低于招标控制价相应值的 85%；清单编码/名称/特征/单位/工程量与招标清单一致",
                            f"需招标控制价清单（附件1/2/3）逐项比对；投标清单 {'p%d-%d' % boq if boq else '未识别'}", ev, req_loc,
                            fix="用清标软件导入招标 XML 与投标 XML 做单价 85% 线与五要素比对")]


RULES = [D01D02D04D06Manual, D03SafetyFee, D05ProvisionalSums]
