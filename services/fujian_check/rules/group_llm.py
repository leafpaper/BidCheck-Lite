"""大模型窗口判断规则：Q-16 其他资格条件、B-09 附加条件、B-10 前附表其他否决项、T-03 技术文件是否本项目。"""
from __future__ import annotations

import re

from services.fujian_check.models import EvidenceWindow, Finding
from services.fujian_check.rules.base import Rule, RuleContext
from services.fujian_check.rules.llm_judge import judge_snippet
from services.fujian_check.rules.retrieval import locate


def _finding_from(rule: Rule, ctx: RuleContext, jr, requirement: str, req_loc, windows: list[EvidenceWindow],
                  fail_missing: str, fix: str) -> Finding:
    ev = [w.loc for w in windows[:3]]
    if jr.quote:
        ev[0].excerpt = jr.quote if ev else ev
    if jr.verdict == "manual":
        return rule.manual(requirement, jr.reason or "需人工判断", ev, req_loc, fix=fix)
    return rule.make(jr.verdict, requirement=requirement, requirement_loc=req_loc, actual=(jr.quote or jr.reason)[:300],
                     evidence=ev, missing="" if jr.verdict == "pass" else (fail_missing + "：" + jr.reason)[:300],
                     fix="" if jr.verdict == "pass" else fix, method="llm", confidence=jr.confidence)


class Q16OtherQualification(Rule):
    id, group, title, severity = "FJ-Q-16", "资格文件否决", "3.1.16 前附表 4.1/4.2/4.3 其他资格条件", "reject"
    method = "llm"
    supersedes = ["compliance"]
    source_clause = "3.1.16"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h = ctx.req.hard
        req_loc = h.sources.get("qualification") or ctx.clause_loc("3.1.16")
        req_text = (h.qualification or "") + "\n不得存在情形：" + (ctx.req.qfb("4.3").content if ctx.req.qfb("4.3") else "")
        windows = locate(ctx, form="投标人基本情况表", window_chars=1500, max_windows=2)
        jr = await judge_snippet(ctx, self, req_text, windows,
                                 question="基本情况表填报的资质等级、安全生产许可证、成立时间等是否明显不满足资格条件？只在片段明确不满足时判 fail。")
        return [_finding_from(self, ctx, jr, "投标人须满足前附表 4.1/4.2 全部资格条件且不存在 4.3 情形", req_loc, windows,
                              "资格条件不满足", "核对资质证书等级与专业")]


class B09UnacceptableConditions(Rule):
    id, group, title, severity = "FJ-B-09", "商务初审否决", "5.1.9 未附带招标文件不能接受的条件", "reject"
    method = "llm"
    supersedes = ["compliance"]
    source_clause = "5.1.9"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        req_loc = ctx.clause_loc("5.1.9")
        windows = locate(ctx, form="投标函附录", window_chars=1500, max_windows=2)
        windows += locate(ctx, form="其他资料", section="商务文件", window_chars=800, max_windows=1, allow_global=False)
        jr = await judge_snippet(ctx, self, "投标文件不得附带招标人不能接受的条件（如对工期、付款、担保、违约金提出与招标文件不同的附加要求）",
                                 windows, question="片段中是否出现投标人单方面附加的、偏离招标文件的条件或保留意见？")
        return [_finding_from(self, ctx, jr, "投标函附录及其他资料不得附加招标人不能接受的条件", req_loc, windows,
                              "附带不可接受条件", "删除附加条件，按招标附录原文填写")]


class B10OtherSubstantive(Rule):
    id, group, title, severity = "FJ-B-10", "商务初审否决", "5.1.10 前附表 34.2 其他含'否决'的实质性要求", "reject"
    method = "llm"
    supersedes = ["mandatoryReq", "compliance"]
    source_clause = "5.1.10"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        clauses = [c for c in ctx.req.rejection if c.group == "须知正文" and "34.2" in c.id]
        if not clauses:
            clauses = [c for c in ctx.req.rejection if c.group == "须知正文"][:6]
        out: list[Finding] = []
        for c in clauses[:5]:
            kws = [k for k in re.findall(r"[一-龥]{2,6}", c.text) if k not in ("否决", "投标", "招标", "评标", "委员会", "投标人")][:4]
            windows = locate(ctx, section="商务文件", patterns=[re.escape(k) for k in kws[:3]], window_chars=900, max_windows=2)
            if not windows:
                out.append(self.manual(c.text[:200], "未检索到相关投标内容", [], c.loc, fix="人工核对该条"))
                continue
            jr = await judge_snippet(ctx, self, c.text, windows, question="投标文件片段是否满足该条要求？无法从片段判断则 warning。")
            out.append(_finding_from(self, ctx, jr, c.text[:200], c.loc, windows, "不满足该实质性要求", "按条款要求补正"))
        return out or [self.na("前附表无其他含否决的补充要求")]


class T03TechContent(Rule):
    id, group, title, severity = "FJ-T-03", "技术文件暗标", "技术文件各段内容属于本项目（无其他项目粘贴残留）", "reject"
    method = "llm"
    supersedes = ["fitScore"]
    source_clause = "4.2"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        segs = ctx.idx.tech_segments
        if not segs:
            return [self.na("未识别到技术文件分段")]
        proj = ctx.req.project_name
        scale = (ctx.req.qfb("1.5").content if ctx.req.qfb("1.5") else "")
        out: list[Finding] = []
        for sg in segs[:5]:
            p0 = sg.page_start or 1
            text = "\n".join(ctx.bdoc.page_text(p) for p in range(p0, min(p0 + 1, sg.page_end or p0) + 1))[:1500]
            w = EvidenceWindow(loc=ctx.bid_page_loc(p0, "技术文件", sg.title), text=text, score=1.0)
            jr = await judge_snippet(ctx, self, f"本项目：{proj}；规模：{scale[:200]}；本段应针对：{sg.title}",
                                     [w], question="这段技术文件内容是否与本项目及该段标题对应？若出现明显属于其他项目/其他工程类型的描述（如本项目为房建却写污水管道、桩基类型不符），判 fail。")
            out.append(_finding_from(self, ctx, jr, f"技术文件段落「{sg.title[:40]}」应针对本项目编制", ctx.clause_loc("4.2"), [w],
                                     "疑似其他项目内容粘贴残留（暗标评审不合格）", "重写为本项目对应内容"))
        return out


RULES = [Q16OtherQualification, B09UnacceptableConditions, B10OtherSubstantive, T03TechContent]
