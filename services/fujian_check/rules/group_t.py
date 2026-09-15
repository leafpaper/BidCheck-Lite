"""FJ-T 技术文件（暗标）。"""
from __future__ import annotations

import re

from services.fujian_check.models import Finding
from services.fujian_check.rules.base import Rule, RuleContext
from services.fujian_check.textnorm import similarity, strip_project_name

GROUP = "技术文件暗标"


class T01BlindMark(Rule):
    id, group, title, severity = "FJ-T-01", GROUP, "暗标：技术文件不得出现投标人名称/法定代表人/项目负责人姓名", "reject"
    source_clause = "4.1"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        rng = ctx.idx.sections.get("技术文件")
        req_loc = ctx.clause_loc("4.1")
        if not rng:
            return [self.na("未识别到技术文件", req_loc)]
        needles = [n for n in {ctx.idx.bidder_name, ctx.idx.legal_rep, ctx.idx.letter.pm_name if ctx.idx.letter else None} if n]
        short = strip_project_name(ctx.idx.bidder_name).replace("有限公司", "").replace("有限责任公司", "")
        if len(short) >= 4:
            needles.append(short)
        hits = []
        for p in range(rng[0], rng[1] + 1):
            t = strip_project_name(ctx.bdoc.page_text(p))
            for n in needles:
                if n and strip_project_name(n) in t:
                    hits.append(ctx.bid_page_loc(p, "技术文件", n))
                    break
        if hits:
            return [self.make("fail", requirement="技术文件所有内容不得出现体现投标人身份的名称、人员姓名、徽标", requirement_loc=req_loc,
                              actual=f"{len(hits)} 页出现身份信息", evidence=hits[:5], missing="暗标违规（否决投标）",
                              fix="删除技术文件中的公司名/人名/logo，重新导出")]
        return [self.make("pass", requirement="技术文件不得出现投标人身份信息", requirement_loc=req_loc,
                          actual=f"p{rng[0]}-{rng[1]} 未出现投标人名称/法定代表人/项目负责人姓名")]


class T02DangerousWorks(Rule):
    id, group, title, severity = "FJ-T-02", GROUP, "危大工程清单段落 ↔ 数据表 4.2 逐项对应（无多余/缺失/重复）", "reject"
    supersedes = ["crossCheck"]
    source_clause = "4.2"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        req_items = ctx.req.dangerous_works
        segs = ctx.idx.tech_segments
        req_loc = req_items[0].loc if req_items else ctx.clause_loc("4.2")
        if not req_items:
            return [self.na("数据表未解析出危大工程清单", req_loc)]
        if not segs:
            return [self.make("fail", requirement=f"技术文件须按 {len(req_items)} 项危大工程清单逐项编制", requirement_loc=req_loc,
                              actual="技术文件未识别到危大工程清单段落", missing="缺危大工程专项内容", fix="按数据表 4.2 清单逐项编制")]
        out: list[Finding] = []
        matched = [False] * len(req_items)
        seen_no: dict[str, int] = {}
        for sg in segs:
            body = re.sub(r"^危大工程清单\s*\d+\s*[:：]", "", sg.title)
            m = re.match(r"^危大工程清单\s*(\d+)", sg.title)
            no = m.group(1) if m else "?"
            seen_no[no] = seen_no.get(no, 0) + 1
            best_i, best_s = -1, 0.0
            for i, it in enumerate(req_items):
                s = similarity(body[:40], str(it.value)[:40])
                if s > best_s:
                    best_i, best_s = i, s
            ev = [ctx.bid_page_loc(sg.page_start, "技术文件", sg.title)] if sg.page_start else []
            if best_s >= 0.6 and not matched[best_i]:
                matched[best_i] = True
                out.append(self.make("pass", requirement=str(req_items[best_i].value)[:120], requirement_loc=req_items[best_i].loc,
                                     actual=sg.title[:120], evidence=ev))
            else:
                out.append(self.make("fail", requirement="技术文件各危大段落必须对应招标清单项", requirement_loc=req_loc,
                                     actual=sg.title[:120] + (f"（清单{no} 重复出现）" if seen_no[no] > 1 else ""), evidence=ev,
                                     missing="该段落不在招标危大清单内或与已有段落重复，疑为其他项目粘贴残留（暗标评审不合格→否决）",
                                     fix="删除或改写为本项目对应的危大工程内容"))
        for i, it in enumerate(req_items):
            if not matched[i]:
                out.append(self.make("fail", requirement=str(it.value)[:120], requirement_loc=it.loc, actual="技术文件无对应段落",
                                     missing="缺该危大工程专项内容", fix="补写该项"))
        return out


class T04DurationInTech(Rule):
    id, group, title, severity = "FJ-T-04", GROUP, "技术文件进度安排的工期数字 ↔ 投标函工期", "minor"
    supersedes = ["consistency"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        rng = ctx.idx.sections.get("技术文件")
        L = ctx.idx.letter
        if not rng:
            return [self.na("未识别到技术文件")]
        want = L.duration_days if L else None
        found = []
        for p in range(rng[0], rng[1] + 1):
            for m in re.finditer(r"(?:总工期|工期)[^\d。]{0,12}(\d{2,4})\s*(?:个)?(?:日历天|天)", ctx.bdoc.page_text(p)):
                found.append((int(m.group(1)), ctx.bid_page_loc(p, "技术文件", m.group(0))))
        if not found:
            return [self.make("warning", requirement=f"技术文件进度安排应与投标函工期（{want} 日历天）一致",
                              actual="技术文件中未出现工期数字", evidence=[], missing="无法交叉核对进度计划与投标函工期",
                              fix="在施工进度计划中写明总工期日历天")]
        nums = {n for n, _ in found}
        ok = want is not None and nums == {want}
        return [self.make("pass" if ok else "warning", requirement=f"技术文件工期 = 投标函工期 {want}",
                          actual="技术文件出现工期：" + "、".join(str(n) for n in sorted(nums)), evidence=[loc for _, loc in found[:3]],
                          missing="" if ok else "技术文件工期与投标函不一致", fix="" if ok else "统一工期数字")]


RULES = [T01BlindMark, T02DangerousWorks, T04DurationInTech]
