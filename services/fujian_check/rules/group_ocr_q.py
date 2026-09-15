"""OCR 支撑的资格文件规则：Q-02 字迹模糊、Q-04/05/10 执照/资质/安许要素与投标人一致、F-06 业绩重复（图像哈希，不用 OCR）。"""
from __future__ import annotations

import re

from services.fujian_check.models import Finding
from services.fujian_check.rules._helpers import level_rank, norm
from services.fujian_check.rules.base import Rule, RuleContext
from services.fujian_check.rules.ocr import is_unreadable, lines_of_type


def _basic_scan_pages(ctx: RuleContext) -> list[int]:
    fr = ctx.form("投标人基本情况表")
    return list(fr.scanned_pages[:6]) if fr else []


class Q02Legibility(Rule):
    id, group, title, severity = "FJ-Q-02", "资格文件否决", "3.1.2 关键扫描件字迹清晰可辨", "reject"
    method = "ocr"

    def ocr_pages_needed(self, ctx: RuleContext) -> list[int]:
        pages = _basic_scan_pages(ctx)
        fr = ctx.form("项目负责人简要情况表")
        if fr:
            pages += fr.scanned_pages[:3]
        return pages

    async def run(self, ctx: RuleContext) -> list[Finding]:
        pages = self.ocr_pages_needed(ctx)
        req_loc = ctx.clause_loc("3.1.2")
        if not pages:
            return [self.na("无关键扫描页", req_loc)]
        texts = {p: ctx.ocr_text.get(p) for p in pages}
        if not any(texts.values()):
            return [self.manual("关键内容字迹清晰", f"扫描页 p{pages[0]}-{pages[-1]} 未 OCR", [ctx.bid_page_loc(p) for p in pages[:3]], req_loc)]
        bad = [p for p, t in texts.items() if t and is_unreadable(t)]
        ev = [ctx.bid_page_loc(p, "扫描件") for p in (bad or pages)[:4]]
        if bad:
            return [self.make("warning", requirement="关键内容不得字迹模糊、无法辨认", requirement_loc=req_loc,
                              actual=f"OCR 无法辨认：p{', p'.join(map(str, bad))}", evidence=ev, missing="可能触发 3.1.2 否决", fix="重新扫描", confidence=0.6)]
        return [self.make("pass", requirement="关键内容不得字迹模糊", requirement_loc=req_loc, actual=f"{len([t for t in texts.values() if t])} 页 OCR 可读", evidence=ev, confidence=0.7)]


class Q10UnitNameMatch(Rule):
    id, group, title, severity = "FJ-Q-10", "资格文件否决", "3.1.10 营业执照/资质证书/安许证单位名称 = 投标人名称", "reject"
    method = "ocr"
    supersedes = ["qualification", "consistency"]

    def ocr_pages_needed(self, ctx: RuleContext) -> list[int]:
        return _basic_scan_pages(ctx)

    async def run(self, ctx: RuleContext) -> list[Finding]:
        pages = _basic_scan_pages(ctx)
        req_loc = ctx.clause_loc("3.1.10")
        bidder = ctx.idx.bidder_name
        if not pages or not bidder:
            return [self.manual("三证单位名称与投标人一致", "无扫描页或未识别投标人", [], req_loc)]
        texts = {p: ctx.ocr_text.get(p, "") for p in pages}
        if not any(texts.values()):
            return [self.manual("三证单位名称与投标人一致", "未 OCR", [ctx.bid_page_loc(p) for p in pages[:3]], req_loc)]
        found = {k: None for k in ("营业执照", "资质证书", "安全生产许可证")}
        mismatch = []
        for p, t in texts.items():
            for k in found:
                for ln in lines_of_type(t, k):
                    found[k] = p
                    names = re.findall(r"([一-龥（）()]{4,40}?(?:公司|集团))", ln)
                    if names and not any(norm(bidder) == norm(n) or norm(bidder) in norm(n) for n in names):
                        mismatch.append(f"{k}(p{p}): {names[0]}")
        ev = [ctx.bid_page_loc(p, "证件扫描件") for p in pages[:3]]
        seen = [k for k, v in found.items() if v]
        if mismatch:
            return [self.make("fail", requirement=f"三证单位名称须与投标人「{bidder}」一致", requirement_loc=req_loc,
                              actual="；".join(mismatch), evidence=ev, missing="名称不一致（否决）", fix="核对证照名称变更并提供证明", confidence=0.7)]
        if not seen:
            return [self.manual(f"三证单位名称须与投标人「{bidder}」一致", "OCR 未识别到三证行", ev, req_loc)]
        return [self.make("pass", requirement=f"三证单位名称须与投标人「{bidder}」一致", requirement_loc=req_loc,
                          actual="OCR 已见：" + "、".join(seen) + "，未发现不一致名称", evidence=ev, confidence=0.7)]


class Q05QualificationLevel(Rule):
    id, group, title, severity = "FJ-Q-05", "资格文件否决", "3.1.5 资质证书等级/专业达标 + 安全生产许可证在有效期", "reject"
    method = "ocr"
    supersedes = ["qualification"]

    def ocr_pages_needed(self, ctx: RuleContext) -> list[int]:
        return _basic_scan_pages(ctx)

    async def run(self, ctx: RuleContext) -> list[Finding]:
        pages = _basic_scan_pages(ctx)
        req_loc = ctx.req.hard.sources.get("qualification") or ctx.clause_loc("3.1.5")
        need_txt = ctx.req.hard.qualification or ""
        need = level_rank(need_txt)
        m = re.search(r"(建筑工程|市政公用工程|公路工程|水利水电工程|电力工程|机电工程|港口与航道工程|矿山工程|冶金工程|石油化工工程|通信工程|铁路工程|民航工程)施工总承包", need_txt)
        need_cat = m.group(1) if m else None
        # 文字层：基本情况表填报的资质
        fr = ctx.form("投标人基本情况表")
        declared = ""
        if fr and fr.page_start:
            t = re.sub(r"\s+", "", ctx.bdoc.page_text(fr.page_start))
            dm = re.search(r"企业资质等级(.{0,120}?)(?:营业执照|项目负责人|开户)", t)
            declared = dm.group(1) if dm else ""
        ev = [ctx.form_loc(fr)] if fr and fr.page_start else []
        ev += [ctx.bid_page_loc(p, "资质证书扫描件") for p in pages[:2]]
        cand = declared
        for p in pages:
            cand += "\n" + ctx.ocr_text.get(p, "")
        if need_cat and need_cat + "施工总承包" in cand:
            seg = cand[cand.index(need_cat + "施工总承包"):][:30]
            got = level_rank(seg)
            ok = need is None or (got is not None and got <= need)
            return [self.make("pass" if ok else ("fail" if got is not None else "warning"),
                              requirement=need_txt[:200], requirement_loc=req_loc, actual=f"投标人具备：{seg}", evidence=ev,
                              missing="" if ok else "资质等级不足或无法识别等级", fix="" if ok else "核对资质证书等级", confidence=0.75)]
        if need_cat:
            return [self.make("fail" if declared else "manual", requirement=need_txt[:200], requirement_loc=req_loc,
                              actual=f"填报/OCR 未见「{need_cat}施工总承包」：{(declared or '无文字层填报')[:120]}", evidence=ev,
                              missing="所需专业资质未见", fix="核对资质证书专业类别", method="ocr" if declared else "manual", confidence=0.6)]
        return [self.manual(need_txt[:200], f"填报：{declared[:150]}", ev, req_loc)]


class F06DuplicateAttachments(Rule):
    id, group, title, severity = "FJ-F-06", "表单齐全性", "业绩/证明材料扫描页重复提交", "minor"
    method = "rule"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        pages = [p for p in ctx.idx.scanned_pages if any(s <= p <= e for name, (s, e) in ctx.idx.sections.items() if name in ("定标文件", "资格文件"))]
        if len(pages) < 2:
            return [self.na("扫描页不足")]
        from services.fujian_check.rules._imghash import page_hash, hamming

        hashes: dict[int, int] = {}
        for p in pages[:120]:
            try:
                hashes[p] = page_hash(ctx.bdoc.path, p)
            except Exception:
                continue
        # 资格文件与定标文件按招标要求会重复提交同一证件，属正常；只在同一节、同一表单归属内查重
        attr = ctx.idx.scanned_attribution
        sec_of = {p: next((n for n, (s, e) in ctx.idx.sections.items() if s <= p <= e), None) for p in hashes}
        dups: list[tuple[int, int]] = []
        items = sorted(hashes.items())
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                pa, pb = items[i][0], items[j][0]
                if pb - pa < 2 or sec_of[pa] != sec_of[pb] or attr.get(pa) != attr.get(pb):
                    continue
                if hamming(items[i][1], items[j][1]) <= 3:
                    dups.append((pa, pb))
        if not dups:
            return [self.make("pass", requirement="同一证明材料不应重复提交", actual=f"{len(hashes)} 个扫描页无重复")]
        ev = [ctx.bid_page_loc(b, "重复扫描页", f"与 p{a} 相同") for a, b in dups[:5]]
        return [self.make("warning", requirement="同一证明材料不应重复提交（凑数嫌疑）", actual=f"{len(dups)} 对相同扫描页：" + "、".join(f"p{a}=p{b}" for a, b in dups[:6]),
                          evidence=ev, missing="重复材料可能被视为凑数或编排混乱", fix="删除重复页")]


RULES = [Q02Legibility, Q05QualificationLevel, Q10UnitNameMatch, F06DuplicateAttachments]
