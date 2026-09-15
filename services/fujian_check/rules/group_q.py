"""FJ-Q 资格文件否决（3.1.x）——确定性与人工标记部分；OCR/LLM 部分在 P3 补充。"""
from __future__ import annotations

import re

from services.fujian_check.models import Finding
from services.fujian_check.rules._helpers import fmt_money
from services.fujian_check.rules.base import Rule, RuleContext

GROUP = "资格文件否决"


class Q03Deposit(Rule):
    id, group, title, severity = "FJ-Q-03", GROUP, "3.1.3 按规定提交投标保证金（金额+凭证）", "reject"
    supersedes = ["deposit"]
    source_clause = "3.1.3"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h, L = ctx.req.hard, ctx.idx.letter
        req_loc = ctx.clause_loc("3.1.3")
        fr = ctx.form("投标保证金")
        ev = [x for x in ([ctx.form_loc(fr)] if fr and fr.page_start else []) + ([L.loc] if L and L.loc else []) if x]
        amount_ok = bool(h.deposit and L and L.deposit and L.deposit.value == h.deposit.value)
        has_voucher = bool(fr and fr.page_start)
        if amount_ok and has_voucher:
            return [self.make("warning" if not fr.scanned_pages else "manual",
                              requirement=f"保证金 {fmt_money(h.deposit)}，凭证扫描件入资格文件（形式：{'、'.join(h.deposit_forms[:4])}）",
                              requirement_loc=req_loc, actual=f"投标函金额一致；凭证在 p{fr.page_start}（扫描件 {len(fr.scanned_pages)} 页）",
                              evidence=ev, missing="凭证金额/收款方/项目编号需 OCR 或人工核对", fix="核对电汇单注明招标项目编号、从基本账户转出",
                              method="manual", confidence=0.6)]
        if not has_voucher:
            return [self.make("fail", requirement=f"保证金 {fmt_money(h.deposit)} 并附凭证", requirement_loc=req_loc,
                              actual="未找到「投标保证金有关单据扫描件」", evidence=ev, missing="缺保证金凭证（3.1.3 否决）", fix="补充凭证扫描件")]
        return [self.make("fail", requirement=f"保证金 {fmt_money(h.deposit)}", requirement_loc=req_loc,
                          actual=f"投标函金额 {fmt_money(L.deposit) if L else '—'}", evidence=ev, missing="金额不一致", fix="按招标金额补缴/修改")]


class Q06JointVenture(Rule):
    id, group, title, severity = "FJ-Q-06", GROUP, "3.1.6 联合体组成符合规定并附协议书", "reject"
    supersedes = ["jointBid"]
    source_clause = "3.1.6"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        from services.fujian_check.rules._helpers import jv_filled

        fr = ctx.form("联合体协议书")
        if not jv_filled(ctx):
            return [self.na("独立投标，无联合体", ctx.clause_loc("3.1.6"))]
        return [self.manual("联合体组成符合招标规定并附协议书", "已提交联合体协议书", [ctx.form_loc(fr)], ctx.clause_loc("3.1.6"))]


class Q07Subcontract(Rule):
    id, group, title, severity = "FJ-Q-07", GROUP, "3.1.7 分包不得违反建筑法（主体/关键工作不得分包）", "reject"
    source_clause = "3.1.7"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        fr = ctx.form("拟分包企业情况")
        req_loc = ctx.clause_loc("3.1.7")
        pol = ctx.req.hard.subcontract_policy or ""
        if not fr or not fr.page_start:
            return [self.na("未提交拟分包企业情况表（本项目可中标后再报）", req_loc)]
        text = ctx.bdoc.page_text(fr.page_start)
        ev = [ctx.form_loc(fr, text[:200])]
        m = re.search(r"分包人名称\s*([一-龥（）()]{4,40}?(?:公司|集团))", text)
        sub_name = m.group(1) if m else ""
        works = re.search(r"拟分包工程\s*(\S{2,60})", text)
        works_txt = works.group(1) if works else ""
        if sub_name and ctx.idx.bidder_name and sub_name == ctx.idx.bidder_name:
            return [self.make("warning", requirement="拟分包企业应为第三方专业分包单位；无分包时应填'/'或不提供", requirement_loc=req_loc,
                              actual=f"分包人名称填写为投标人自身：{sub_name}", evidence=ev,
                              missing="表单填写异常，评标可能质疑", fix="无分包计划则整表填'/'或删除本表")]
        if works_txt and re.search(r"主体|基础|结构|关键", works_txt):
            return [self.make("fail", requirement="不得将主体、关键性工作分包", requirement_loc=req_loc, actual=f"拟分包工程：{works_txt}",
                              evidence=ev, missing="违法分包（否决）", fix="删除主体/关键工作的分包计划")]
        if not sub_name or works_txt in ("/", ""):
            return [self.make("pass", requirement="分包应符合建筑法与前附表分包规定", requirement_loc=req_loc, actual="未计划分包（表内为'/'）", evidence=ev)]
        return [self.manual("分包范围符合建筑法及前附表规定", f"分包人 {sub_name}；拟分包 {works_txt}", ev, req_loc)]


class Q09SimilarProjects(Rule):
    id, group, title, severity = "FJ-Q-09", GROUP, "3.1.9 类似工程业绩满足数据表第6项", "reject"
    source_clause = "3.1.9"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        need = ctx.req.hard.similar_projects_required
        req_loc = ctx.clause_loc("3.1.9")
        if need in (0, None):
            return [self.na(f"招标要求类似工程业绩 {need if need is not None else '未设'} 个，投标阶段不评审", req_loc)]
        fr = ctx.form("类似工程业绩")
        if not fr or not fr.page_start:
            return [self.make("fail", requirement=f"类似工程业绩 ≥ {need} 个", requirement_loc=req_loc, actual="未提交业绩汇总表",
                              missing="缺业绩（否决）", fix="补充业绩表及合同/验收证明")]
        return [self.manual(f"类似工程业绩 ≥ {need} 个（须附合同与竣工验收证明，四库一平台可查）", "已提交业绩表，需人工核对数量与证明",
                            [ctx.form_loc(fr)], req_loc)]


class Q11ProhibitedSituations(Rule):
    id, group, title, severity = "FJ-Q-11", GROUP, "3.1.11 不存在投标须知 4.3 款规定情形（承诺+查询截图）", "reject"
    supersedes = ["disqualification"]
    source_clause = "3.1.11"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        req_loc = ctx.clause_loc("3.1.11")
        L = ctx.idx.letter
        t = "".join(ctx.bdoc.page_text(p) for p in range(L.loc.page, (L.loc.page_end or L.loc.page) + 1)) if L and L.loc else ""
        t = re.sub(r"\s+", "", t)
        promised = bool(re.search(r"不存在.{0,30}4\.3|4\.3.{0,30}情形", t))
        fr = ctx.form("诚信承诺函")
        ev = [x for x in ([L.loc] if L and L.loc else []) + ([ctx.form_loc(fr)] if fr and fr.page_start else []) if x]
        award_ev = [ctx.form_loc(f) for f in ctx.idx.forms if f.form and f.form.section == "定标文件" and ("失信" in f.form.title or "违法失信" in f.form.title) and f.page_start]
        return [self.make("pass" if promised else "warning",
                          requirement="投标人不得存在须知 4.3 款 12 项情形（黑名单、安全标准化 D 级、利害关系、串通等）", requirement_loc=req_loc,
                          actual=("投标函已声明不存在 4.3 款情形" if promised else "投标函未见 4.3 款声明") + f"；诚信承诺函 {'已定位' if fr and fr.page_start else '疑似扫描件'}；失信/违法失信查询截图 {len(award_ev)} 份",
                          evidence=ev + award_ev, missing="" if promised else "缺声明或为扫描件，需人工核对",
                          fix="" if promised else "核对投标函第5条与诚信承诺函", method="rule" if promised else "manual",
                          confidence=1.0 if promised else 0.6)]


class Q12CreditScore(Rule):
    id, group, title, severity = "FJ-Q-12", GROUP, "3.1.12 信用综合评价分值不低于 60 分", "reject"
    source_clause = "3.1.12"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        if ctx.req.hard.credit_score_applied is False:
            return [self.na("本项目不应用信用综合评价分值", ctx.clause_loc("3.1.12"))]
        return [self.manual("企业季度信用得分 ≥ 60", "需在福建省建筑施工企业信用综合评价系统查询截标时得分", [], ctx.clause_loc("3.1.12"))]


class Q13XmlInfo(Rule):
    id, group, title, severity = "FJ-Q-13", GROUP, "3.1.13 XML 清单已记录软硬件信息且未被篡改", "reject"
    method = "manual"
    supersedes = ["ebidSubmit"]
    source_clause = "3.1.13"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        fr = ctx.form("编制人员情况表")
        return [self.manual("已标价清单 XML 须按须知 20.7 记录编制软硬件信息，平台校验不得被篡改",
                            "PDF 无法判定；请在电子标书生成器/平台校验", [ctx.form_loc(fr)] if fr and fr.page_start else [], ctx.clause_loc("3.1.13"))]


class Q14Collusion(Rule):
    id, group, title, severity = "FJ-Q-14", GROUP, "3.1.14 无雷同情形（20.6 (1)-(3)）", "reject"
    method = "manual"
    supersedes = ["duplicate"]
    source_clause = "3.1.14"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        return [self.manual("不得出现投标须知 20.6 款 (1)-(3) 雷同情形（同一编制机器码、同一 IP、同一造价软件锁等）",
                            "需与其他投标人比对，单份文件无法判定；请自查电子标书生成器的机器信息", [], ctx.clause_loc("3.1.14"))]


class Q15OwnCa(Rule):
    id, group, title, severity = "FJ-Q-15", GROUP, "3.1.15 使用本单位企业数字证书加密", "reject"
    method = "manual"
    supersedes = ["ebidSubmit", "signature"]
    source_clause = "3.1.15"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        if ctx.req.hard.ca_own_only is False:
            return [self.na("招标未限定本单位 CA", ctx.clause_loc("3.1.15"))]
        return [self.manual("必须用投标人本单位企业数字证书加密投标文件（不得用法定代表人或他人 CA）",
                            "PDF 无法判定；请在平台上传前确认所用 CA 为本单位企业证书", [], ctx.clause_loc("3.1.15"))]


RULES = [Q03Deposit, Q06JointVenture, Q07Subcontract, Q09SimilarProjects, Q11ProhibitedSituations, Q12CreditScore,
         Q13XmlInfo, Q14Collusion, Q15OwnCa]
