"""FJ-P 前附表硬值 ↔ 投标文件承诺。"""
from __future__ import annotations

import re

from services.fujian_check.models import Finding
from services.fujian_check.rules._helpers import cover_pages, fmt_money, level_rank, norm, page_contains, pct
from services.fujian_check.rules.base import Rule, RuleContext

GROUP = "前附表硬值"


class P01Duration(Rule):
    id, group, title, severity = "FJ-P-01", GROUP, "工期：投标函承诺 ≤ 前附表计划工期", "reject"
    supersedes = ["compliance"]
    source_clause = "5.1.8"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h, L = ctx.req.hard, ctx.idx.letter
        req_loc = ctx.qfb_loc("2.1")
        if h.duration_days is None:
            return [self.manual("计划工期", "招标前附表未抽到工期", [], req_loc)]
        if L is None or L.duration_days is None:
            return [self.make("fail", requirement=f"总工期 {h.duration_days} 日历天", requirement_loc=req_loc,
                              actual="投标函未写明工期", evidence=[L.loc] if L and L.loc else [],
                              missing="投标函缺工期承诺（5.1.8 否决项）", fix="在投标函第1条填写工期日历天")]
        ok = L.duration_days <= h.duration_days
        extra = ""
        if h.quota_duration_days and L.quota_duration_days and h.quota_duration_days != L.quota_duration_days:
            extra = f"；定额工期不一致（招标 {h.quota_duration_days} / 投标 {L.quota_duration_days}）"
            ok = False
        return [self.make("pass" if ok else "fail",
                          requirement=f"总工期 {h.duration_days} 日历天" + (f"，定额工期 {h.quota_duration_days}" if h.quota_duration_days else ""),
                          requirement_loc=req_loc,
                          actual=f"投标函工期 {L.duration_days} 日历天" + (f"，定额工期 {L.quota_duration_days}" if L.quota_duration_days else "") + extra,
                          evidence=[L.loc] if L.loc else [],
                          missing="" if ok else "投标工期超过招标要求或定额工期不一致（5.1.8 否决）",
                          fix="" if ok else "把投标函工期改为不超过招标计划工期，定额工期照抄前附表")]


class P02Quality(Rule):
    id, group, title, severity = "FJ-P-02", GROUP, "质量标准：投标函承诺不低于招标要求", "reject"
    supersedes = ["compliance"]
    source_clause = "5.1.8"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h, L = ctx.req.hard, ctx.idx.letter
        req_loc = ctx.qfb_loc("2.2")
        if not h.quality:
            return [self.manual("质量要求", "招标前附表未抽到质量要求", [], req_loc)]
        if L is None or not L.quality:
            return [self.make("warning", requirement=h.quality, requirement_loc=req_loc, actual="投标函未抽到质量承诺",
                              evidence=[L.loc] if L and L.loc else [], missing="需人工核对投标函质量标准", fix="")]
        same = norm(h.quality) == norm(L.quality) or ("合格" in h.quality and "合格" in L.quality)
        return [self.make("pass" if same else "warning", requirement=h.quality, requirement_loc=req_loc, actual=L.quality,
                          evidence=[L.loc] if L.loc else [], missing="" if same else "质量承诺与招标表述不一致，需人工判断是否低于要求",
                          fix="" if same else "按前附表 2.2 原文填写质量标准")]


class P03Validity(Rule):
    id, group, title, severity = "FJ-P-03", GROUP, "投标有效期 ≥ 招标要求天数", "major"
    supersedes = ["validity"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h, L = ctx.req.hard, ctx.idx.letter
        req_loc = ctx.qfb_loc("17.1")
        if h.validity_days is None:
            return [self.manual("投标有效期", "招标未抽到有效期", [], req_loc)]
        if L is None or L.validity_days is None:
            return [self.make("warning", requirement=f"投标有效期 {h.validity_days} 天", requirement_loc=req_loc,
                              actual="投标函未写明有效期天数（福建平台投标函模板仅承诺'在投标有效期内不撤销'）",
                              evidence=[L.loc] if L and L.loc else [],
                              missing="无法机器核对天数；模板无栏位时属正常", fix="如招标要求投标函写明天数，请补填")]
        ok = L.validity_days >= h.validity_days
        return [self.make("pass" if ok else "fail", requirement=f"投标有效期 {h.validity_days} 天", requirement_loc=req_loc,
                          actual=f"投标函承诺 {L.validity_days} 天", evidence=[L.loc] if L.loc else [],
                          missing="" if ok else "有效期短于招标要求", fix="" if ok else "改为不少于招标要求天数")]


class P04Deposit(Rule):
    id, group, title, severity = "FJ-P-04", GROUP, "投标保证金金额与招标要求一致", "reject"
    supersedes = ["deposit"]
    source_clause = "3.1.3"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h, L = ctx.req.hard, ctx.idx.letter
        req_loc = ctx.qfb_loc("18.1") or ctx.req.hard.sources.get("deposit")
        if h.deposit is None:
            return [self.manual("投标保证金金额", "招标未抽到保证金金额", [], req_loc)]
        fr = ctx.form("投标保证金")
        ev = [x for x in ([L.loc] if L and L.loc else []) + ([ctx.form_loc(fr)] if fr else []) if x]
        forms = "、".join(h.deposit_forms[:5])
        if L is None or L.deposit is None:
            return [self.make("warning", requirement=f"保证金 {fmt_money(h.deposit)}（形式：{forms}）", requirement_loc=req_loc,
                              actual="投标函未抽到保证金金额", evidence=ev, missing="需人工核对投标函第3条与单据", fix="")]
        ok = L.deposit.value == h.deposit.value
        return [self.make("pass" if ok else "fail", requirement=f"保证金 {fmt_money(h.deposit)}（形式：{forms}）",
                          requirement_loc=req_loc, actual=f"投标函承诺 {fmt_money(L.deposit)}", evidence=ev,
                          missing="" if ok else "保证金金额与招标要求不一致（3.1.3 资格否决）",
                          fix="" if ok else "改为招标规定金额并核对缴纳凭证")]


class P05ProjectIdentity(Rule):
    id, group, title, severity = "FJ-P-05", GROUP, "项目名称与招标项目编号一致", "major"
    supersedes = ["consistency"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        code, name = ctx.req.project_code, ctx.req.project_name
        req_loc = ctx.qfb_loc("1.2")
        if not code and not name:
            return [self.manual("项目名称/编号", "招标未抽到项目标识", [], req_loc)]
        pages = cover_pages(ctx)
        ev = []
        ok_code = ok_name = True
        if code:
            loc = page_contains(ctx, pages, re.escape(code))
            ok_code = loc is not None
            if loc:
                ev.append(loc)
        if name:
            found = any(norm(name) in norm(ctx.bdoc.page_text(p)) for p in pages)
            ok_name = found
            if found:
                p = next(p for p in pages if norm(name) in norm(ctx.bdoc.page_text(p)))
                ev.append(ctx.bid_page_loc(p, "封面", name))
        ok = ok_code and ok_name
        return [self.make("pass" if ok else "fail", requirement=f"{name} / {code}", requirement_loc=req_loc,
                          actual=("编号" + ("已" if ok_code else "未") + "出现；名称" + ("已" if ok_name else "未") + "出现"),
                          evidence=ev, missing="" if ok else "封面项目名称或编号与招标不一致",
                          fix="" if ok else "封面与投标函抬头改为招标文件的项目名称与编号")]


class P06PerformanceBond(Rule):
    id, group, title, severity = "FJ-P-06", GROUP, "履约担保比例：投标函附录 ↔ 前附表 29.1", "major"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h = ctx.req.hard
        req_loc = ctx.qfb_loc("29.1")
        want = pct(h.performance_bond)
        bv = ctx.idx.appendix.get("承包人履约担保金额")
        if want is None:
            return [self.na("前附表未抽到履约担保比例", req_loc)]
        if bv is None:
            return [self.make("warning", requirement=f"履约担保 {want:g}%", requirement_loc=req_loc, actual="投标附录无履约担保行",
                              missing="附录缺履约担保金额", fix="补填")]
        got = pct(str(bv.value))
        ok = got == want
        return [self.make("pass" if ok else "fail", requirement=f"履约担保 {want:g}%", requirement_loc=req_loc,
                          actual=str(bv.value), evidence=[bv.loc] if bv.loc else [],
                          missing="" if ok else "履约担保比例与招标不一致", fix="" if ok else "改回招标规定比例")]


class P07ProjectManagerQualification(Rule):
    id, group, title, severity = "FJ-P-07", GROUP, "项目负责人：建造师等级达标 + 安B证 + 本企业在岗", "reject"
    supersedes = ["qualification"]
    source_clause = "3.1.8"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h = ctx.req.hard
        st = ctx.req.staffing
        req_loc = h.sources.get("pm_requirement") or ctx.qfb_loc("4.1")
        need_rank = level_rank(h.pm_requirement) or (level_rank(st.roles["项目负责人"].cert) if st and "项目负责人" in st.roles else None)
        pm = next((p for p in (ctx.idx.personnel.people if ctx.idx.personnel else []) if p.post == "项目负责人"), None)
        fr = ctx.form("项目负责人简要情况表")
        ev = [x for x in ([ctx.form_loc(fr)] if fr else []) if x]
        if pm is None:
            return [self.make("fail", requirement=h.pm_requirement or "项目负责人资格", requirement_loc=req_loc,
                              actual="未抽到项目负责人", evidence=ev, missing="人员表/简要情况表缺项目负责人", fix="补充表单七与人员表")]
        got_rank = level_rank(pm.cert)
        parts = [f"{pm.name}，{pm.cert or '等级未知'}"]
        verdict = "pass"
        missing = []
        if need_rank is not None and got_rank is not None:
            if got_rank > need_rank:
                verdict = "fail"
                missing.append(f"建造师等级 {pm.cert} 低于要求")
        elif need_rank is not None:
            verdict = "manual"
            missing.append("建造师等级未能机器识别")
        text = "\n".join(ctx.bdoc.page_text(p) for p in range(fr.page_start, (fr.page_end or fr.page_start) + 1)) if fr and fr.page_start else ""
        has_b = bool(re.search(r"安全生产考核合格证|安\s*B|闽建安B", text))
        parts.append("安B证" + ("已见" if has_b else "未见"))
        if not has_b and verdict == "pass":
            verdict = "warning"
            missing.append("简要情况表文字层未见安B证书号（可能仅扫描件）")
        parts.append("本企业在岗：需核对注册单位（扫描件）")
        return [self.make(verdict, requirement=h.pm_requirement or "项目负责人资格", requirement_loc=req_loc,
                          actual="；".join(parts), evidence=ev, missing="；".join(missing),
                          fix="" if verdict == "pass" else "核对建造师注册证书等级/专业、安B证与注册单位")]


class P08JointVenture(Rule):
    id, group, title, severity = "FJ-P-08", GROUP, "联合体投标：是否允许 / 成员数", "reject"
    supersedes = ["jointBid"]
    source_clause = "3.1.6"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        jv = ctx.req.hard.joint_venture
        req_loc = ctx.req.hard.sources.get("joint_venture") or ctx.qfb_loc("4.1")
        from services.fujian_check.rules._helpers import jv_filled

        fr = ctx.form("联合体协议书")
        if not jv_filled(ctx):
            return [self.na("投标人以独立投标人身份投标，联合体条款不适用", req_loc)]
        if jv and not jv.allowed:
            return [self.make("fail", requirement="本项目不接受联合体投标", requirement_loc=req_loc, actual="投标文件含联合体协议",
                              evidence=[ctx.form_loc(fr)], missing="联合体不被接受（3.1.6 否决）", fix="改为独立投标")]
        return [self.manual("联合体协议书内容符合招标要求（成员数、牵头人资质）", "已提交联合体协议，需人工核对",
                            [ctx.form_loc(fr)] if fr else [], req_loc)]


class P10AwardFile(Rule):
    id, group, title, severity = "FJ-P-10", GROUP, "评定分离项目：已提交定标文件", "major"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        if not ctx.req.profile.variant.get("评定分离"):
            return [self.na("非评定分离项目")]
        req_loc = ctx.qfb_loc("25.3") or ctx.qfb_loc("26.1")
        rng = ctx.idx.sections.get("定标文件")
        if rng:
            return [self.make("pass", requirement="评定分离项目须按第8章附件2格式提交定标文件", requirement_loc=req_loc,
                              actual=f"定标文件 p{rng[0]}-{rng[1]}", evidence=[ctx.bid_page_loc(rng[0], "定标文件封面")])]
        return [self.make("fail", requirement="评定分离项目须提交定标文件", requirement_loc=req_loc, actual="未找到定标文件",
                          missing="缺定标文件", fix="按附件2 格式编制定标文件")]


RULES = [P01Duration, P02Quality, P03Validity, P04Deposit, P05ProjectIdentity, P06PerformanceBond,
         P07ProjectManagerQualification, P08JointVenture, P10AwardFile]
