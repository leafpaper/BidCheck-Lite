"""GC-* 政府采购竞争性磋商（工程类）规则组。只在 profile=fujian_gov_cs 时装载。"""
from __future__ import annotations

import re

from services.fujian_check.dates import add_months, month_key, months_covered, parse_date
from services.fujian_check.models import Finding, Location, Sourced
from services.fujian_check.rules._helpers import fmt_money, level_rank, norm, pct
from services.fujian_check.rules.base import Rule, RuleContext
from services.fujian_check.rules.ocr import lines_of_type

PROFILES = {"fujian_gov_cs"}


def _commit(ctx: RuleContext, key: str):
    return ctx.idx.appendix.get(f"承诺:{key}")


def _find_in_bid(ctx: RuleContext, pattern: str, label: str = "响应文件") -> Location | None:
    rx = re.compile(pattern)
    for p in range(1, ctx.bdoc.n_pages + 1):
        t = re.sub(r"\s+", "", to_halfwidth_safe(ctx.bdoc.page_text(p)))
        m = rx.search(t)
        if m:
            s = max(0, m.start() - 40)
            return ctx.bid_page_loc(p, label, t[s:m.end() + 80])
    return None


def _tender_sentence(ctx: RuleContext, keyword: str) -> tuple[str, Location] | None:
    """招标文件里含关键词的第一句（第二/三章优先）。"""
    ranges = [(s.page_start, s.page_end) for s in ctx.req.sections if s.chapter in (2, 3)] or [(1, ctx.tdoc.n_pages)]
    for a, b in ranges:
        for p in range(a, b + 1):
            t = re.sub(r"\s+", "", to_halfwidth_safe(ctx.tdoc.page_text(p)))
            i = t.find(keyword)
            if i >= 0:
                s = max(t.rfind("。", 0, i) + 1, t.rfind("；", 0, i) + 1, 0)
                e = t.find("。", i)
                sent = t[s: e + 1 if e > 0 else i + 120][:220]
                return sent, Location(doc="tender", page=p, section="第三章 采购内容及要求", clause=keyword, excerpt=sent)
    return None


class GCF01Attachments(Rule):
    id, group, title, severity = "GC-F-01", "表单齐全性", "第五章 附件1-8 及子附件齐全", "reject"
    supersedes = ["docIntegrity"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        req = ctx.req.forms_in("响应文件")
        if not req:
            return [self.na("第五章未解析出附件清单")]
        out: list[Finding] = []
        located = 0
        for fr in ctx.idx.forms:
            f = fr.form
            if not f or f.section != "响应文件":
                continue
            label = f"附件{f.no} {f.title}"
            if fr.match_kind in ("exact", "alias", "fuzzy"):
                located += 1
                continue
            if f.optional and fr.match_kind in ("scanned", "missing"):
                out.append(self.make("na", requirement=f"「{label}」为可选（若有）", requirement_loc=f.loc, actual="未提交或为扫描件"))
                continue
            if fr.match_kind == "scanned":
                out.append(self.make("warning", requirement=f"须提交「{label}」", requirement_loc=f.loc,
                                     actual=f"文字层未见标题，p{fr.page_start}-{fr.page_end} 为扫描页", evidence=[ctx.bid_page_loc(fr.page_start, label)],
                                     missing="需人工确认扫描件即为该附件", method="manual", confidence=0.6))
            elif f.optional:
                out.append(self.make("na", requirement=f"「{label}」为可选（若有）", requirement_loc=f.loc, actual="未提交"))
            else:
                out.append(self.make("fail", requirement=f"须提交「{label}」", requirement_loc=f.loc, actual="未找到",
                                     missing=f"缺附件{f.no}（须知 14.2.1 情形1：资格证明文件不全）", fix="按第五章格式补齐"))
        v = "fail" if any(x.verdict == "fail" for x in out) else ("warning" if any(x.verdict == "warning" for x in out) else "pass")
        out.insert(0, self.make(v, requirement=f"第五章 {len(req)} 项附件齐全", actual=f"已定位 {located} 项", missing="" if v == "pass" else "有缺失或未确认附件"))
        return out


class GCQ01Materials(Rule):
    id, group, title, severity = "GC-Q-01", "资格文件否决", "前附表第1项 资格材料 11 项（资格承诺制可替代一般资格材料）", "reject"
    method = "ocr"
    supersedes = ["qualification", "disqualification"]

    def _fin_page(self, ctx: RuleContext) -> int | None:
        fr = next((f for f in ctx.idx.forms if f.form and f.form.no == "3-4-2" and f.page_start), None)
        if fr and fr.scanned_pages:
            return fr.scanned_pages[0]
        return None

    def ocr_pages_needed(self, ctx: RuleContext) -> list[int]:
        p = self._fin_page(ctx)
        return [p] if p else []

    def _finance_year(self, ctx: RuleContext) -> Finding | None:
        p = self._fin_page(ctx)
        t = ctx.ocr_text.get(p, "") if p else ""
        if not p or not t or t.startswith("[OCR失败]"):
            return None
        dl = parse_date(ctx.req.hard.deadline)
        years = sorted({int(y) for y in re.findall(r"(20\d{2})\s*年?\s*(?:度|年报|审计|财务)", t)} | {int(y) for y in re.findall(r"(20\d{2})\s*年度", t)})
        if not years or not dl:
            return None
        ok = max(years) >= dl.year - 1
        return self.make("pass" if ok else "warning", requirement="财务状况报告应为上一年度（或近期）审计报告/财务报表",
                         actual=f"扫描件 p{p} 识别到年度：{', '.join(map(str, years))}", evidence=[ctx.bid_page_loc(p, "财务状况报告")],
                         missing="" if ok else f"最新年度 {max(years)} 早于 {dl.year - 1}，可能不被认可", fix="" if ok else "补充最近年度审计报告", method="ocr", confidence=0.6)

    async def run(self, ctx: RuleContext) -> list[Finding]:
        out = await self._materials(ctx)
        fy = self._finance_year(ctx)
        if fy:
            out.append(fy)
        return out

    async def _materials(self, ctx: RuleContext) -> list[Finding]:
        quals = [f for f in ctx.req.forms_in("资格文件") if f.no.isdigit()]
        if not quals:
            return [self.na("未解析出资格材料清单")]
        promise = any(f.form and f.form.no == "3-4-1" and f.page_start for f in ctx.idx.forms)
        out: list[Finding] = []
        att_map = {"1": "1", "2": "3-3", "3": "3-4-2", "4": "3-4-2", "5": "3-4-2", "6": "3-4-2", "7": "3-4-2", "8": "3-4-2",
                   "9": "7-1-1", "10": "3-5", "11": "3-6"}

        def _att(no: str):
            return next((f for f in ctx.idx.forms if f.form and f.form.no == no), None)
        for q in quals:
            t = q.title
            key = None
            if "声明" in t and "磋商响应" in t:
                key = r"磋商响应声明"
            elif "授权书" in t:
                key = r"单位负责人授权书|授权委托书"
            elif "营业执照" in t:
                key = r"营业执照"
            elif "财务" in t:
                key = r"财务状况报告|资信证明|审计报告"
            elif "税收" in t:
                key = r"税收|纳税"
            elif "社会保障" in t:
                key = r"社会保障|社保|社会保险"
            elif "设备和专业技术能力" in t:
                key = r"设备和专业技术能力"
            elif "重大违法" in t:
                key = r"重大违法记录"
            elif "中小企业" in t:
                key = r"中小企业声明函"
            elif "信用记录" in t:
                key = r"信用中国|信用记录"
            elif "联合体" in t:
                key = None
            if key is None:
                out.append(self.na(f"「{t}」不适用（独立响应）", q.loc))
                continue
            loc = None
            att = _att(att_map.get(q.no, ""))
            if att and att.page_start and att.match_kind in ("exact", "alias"):
                loc = ctx.form_loc(att)
            else:
                for p in range(1, ctx.bdoc.n_pages + 1):
                    if re.search(key, ctx.bdoc.page_text(p)):
                        loc = ctx.bid_page_loc(p, t)
                        break
            covered_by_promise = promise and q.no in ("3", "4", "5", "6", "7", "8")
            if loc is None and att and att.match_kind == "scanned":
                out.append(self.make("warning", requirement=t, requirement_loc=q.loc, actual=f"文字层未见，p{att.page_start}-{att.page_end} 为扫描页，疑似扫描件提交",
                                     evidence=[ctx.bid_page_loc(att.page_start, t)], missing="需人工确认扫描件", method="manual", confidence=0.6))
                continue
            if loc:
                extra = ""
                if "中小企业" in t:
                    ok_ver = "工程" in ctx.bdoc.page_text(loc.page)[:200]
                    extra = "（工程/服务版）" if ok_ver else "（版本疑为货物版，需核对）"
                out.append(self.make("pass" if not extra.startswith("（版本疑") else "warning", requirement=t, requirement_loc=q.loc,
                                     actual="已提供" + extra, evidence=[loc]))
            elif covered_by_promise:
                out.append(self.make("pass", requirement=t, requirement_loc=q.loc, actual="采用资格承诺函替代（附件3-4-1 已提供）",
                                     evidence=[ctx.form_loc(next(f for f in ctx.idx.forms if f.form and f.form.no == "3-4-1"))]))
            else:
                out.append(self.make("fail" if not q.optional else "na", requirement=t, requirement_loc=q.loc, actual="未见",
                                     missing="资格证明文件不全（情形1）", fix="补充该项或提供资格承诺函"))
        return out


class GCQ02Special(Rule):
    id, group, title, severity = "GC-Q-02", "资格文件否决", "特定资格：资质等级+安许证 / 项目负责人建造师+安B / 中小企业声明函", "reject"
    method = "ocr"
    supersedes = ["qualification"]

    def _cert_pages(self, ctx: RuleContext) -> list[int]:
        pages: list[int] = []
        for f in ctx.idx.forms:
            if f.form and (f.form.no == "3" or f.form.no.startswith("3-")) and f.page_start and f.match_kind != "scanned":
                pages += f.scanned_pages
        return sorted(set(pages))[:16]

    def ocr_pages_needed(self, ctx: RuleContext) -> list[int]:
        return self._cert_pages(ctx)

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h = ctx.req.hard
        out: list[Finding] = []
        pages = self._cert_pages(ctx)
        ocr_pages = {p: ctx.ocr_text.get(p, "") for p in pages}
        failed = [p for p, t in ocr_pages.items() if t.startswith("[OCR失败]")]
        ocr = "\n".join(t for p, t in ocr_pages.items() if t and p not in failed)
        ocr_note = f"（OCR 失败 {len(failed)} 页：{ocr_pages[failed[0]][:40]}，请检查 ALIYUN_API_KEY）" if failed else ""
        layer = "\n".join(ctx.bdoc.page_text(p) for p in range(1, min(120, ctx.bdoc.n_pages) + 1))
        ev = [ctx.bid_page_loc(p, "资格证明扫描件") for p in pages[:3]]
        # 资质 + 安许
        if h.qualification:
            need_rank = level_rank(h.qualification)
            mcat = re.search(r"([一-龥]{2,12}工程专业承包|[一-龥]{2,12}施工总承包)", h.qualification)
            cat = re.sub(r"^(?:.*?(?:具备|具有|取得|持有|提供))?(?:有效的?|的)?", "", mcat.group(1)) if mcat else None
            hit = [ln for ln in lines_of_type(ocr + "\n" + layer, "资质证书", "专业承包", "总承包") if cat and cat[:6] in ln]
            anxu = bool(re.search(r"安全生产许可证", ocr + layer))
            if not ocr and not hit:
                out.append(self.manual(h.qualification[:160], "证书为扫描件，未启用 OCR" + ocr_note, ev, h.sources.get("qualification")))
            else:
                got = min([r for r in (level_rank(ln) for ln in hit) if r is not None], default=None)
                problems = []
                if not hit:
                    problems.append(f"未识别到「{cat}」资质证书")
                elif need_rank is not None and got is not None and got > need_rank:
                    problems.append("资质等级低于要求")
                if not anxu:
                    problems.append("未见安全生产许可证")
                out.append(self.make("fail" if problems else "pass", requirement=h.qualification[:160], requirement_loc=h.sources.get("qualification"),
                                     actual=("；".join(hit[:2])[:200] or "—") + ("；安许证已见" if anxu else ""), evidence=ev,
                                     missing="；".join(problems), fix="补充有效资质证书与安全生产许可证复印件", confidence=0.7))
        # 项目负责人
        if h.pm_requirement:
            L = ctx.idx.letter
            cert = (L.pm_cert_no if L else "") or ""
            pm_ok = bool(re.search(r"闽?\d{10,}", cert))
            need_b = bool(re.search(r"安全生产考核|B类|B证", h.pm_requirement))
            b_ok = (not need_b) or bool(re.search(r"安全生产考核合格证|安B|B类|B证", ocr + layer))
            out.append(self.make("pass" if pm_ok and b_ok else ("manual" if not ocr else "warning"), requirement=h.pm_requirement[:200], requirement_loc=h.sources.get("pm_requirement"),
                                 actual=f"开标一览表项目经理 {L.pm_name if L else '—'} 证书 {cert or '—'}" + (f"；安B证{'已见' if b_ok else '未见'}" if need_b else "") + ("" if ocr else ocr_note or "（证书扫描件未 OCR）"),
                                 evidence=([L.loc] if L and L.loc else []) + ev, missing="" if pm_ok and b_ok else "需核对建造师专业/等级与安B证扫描件",
                                 fix="按磋商文件要求核对：" + h.pm_requirement[:80], confidence=0.6))
        # 中小企业声明函：只在磋商文件专门面向中小企业、或资格材料清单列了声明函时才要求
        sme_required = bool(ctx.req.profile.variant.get("专门面向中小企业")) or any("中小企业" in f.title for f in ctx.req.forms_in("资格文件"))
        fr = next((f for f in ctx.idx.forms if f.page_start and (("中小企业" in f.matched_title) or (f.form and f.form.no == "7-1-1"))), None)
        sme_loc = next((f.loc for f in ctx.req.forms_in("资格文件") if "中小企业" in f.title), None)
        if fr:
            t = ctx.bdoc.page_text(fr.page_start)
            ok = "工程" in t[:120] or "工程、服务" in t
            m = re.search(r"属于\s*([中小微]型企业)", re.sub(r"\s+", "", t))
            out.append(self.make("pass" if ok else "warning", requirement="中小企业声明函（工程版）" + ("，本项目专门面向中小企业" if sme_required else ""), requirement_loc=sme_loc,
                                 actual=f"已提供{'（工程、服务版）' if ok else '（版本需核对）'}，声明为 {m.group(1) if m else '未识别'}",
                                 evidence=[ctx.form_loc(fr)], missing="" if ok else "声明函版本可能不符"))
        elif sme_required:
            out.append(self.make("fail", requirement="本项目专门面向中小企业：须提供中小企业声明函", requirement_loc=sme_loc, actual="未找到", missing="缺中小企业声明函（资格审查不合格）", fix="按附件格式填写中小企业声明函（工程版）"))
        else:
            out.append(self.na("磋商文件未要求中小企业声明函", sme_loc))
        return out


class GCV01Invalid(Rule):
    id, group, title, severity = "GC-V-01", "商务初审否决", "须知 14.2.1 无效情形 1-7 逐条", "reject"
    supersedes = ["compliance", "validity", "deposit", "pricing"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h, L = ctx.req.hard, ctx.idx.letter
        out: list[Finding] = []
        for c in [c for c in ctx.req.rejection if c.id.startswith("14.2.1-")]:
            n = c.id.split("-")[1]
            if n == "1":
                continue  # GC-Q-01 覆盖
            if n == "2":
                out.append(self.manual(c.text, "电子响应文件以 CA 电子印章为准（表2 ⑦b），PDF 无法判定", [], c.loc, fix="上传前确认全部需盖章处已加盖电子印章"))
            elif n == "3":
                if h.deposit is not None and h.deposit.value == 0:
                    out.append(self.na("本项目磋商保证金为 0 元", c.loc))
                else:
                    fr = next((f for f in ctx.idx.forms if f.form and f.form.no == "4" and f.page_start), None)
                    out.append(self.manual(c.text, f"保证金 {fmt_money(h.deposit)}；凭证附件4 {'已定位 p%d' % fr.page_start if fr else '未见'}", [ctx.form_loc(fr)] if fr else [], c.loc))
            elif n == "4":
                decl = next((f for f in ctx.idx.forms if f.form and f.form.no == "1" and f.page_start), None) or \
                    next((f for f in ctx.idx.forms if f.page_start and re.search(r"响应声明|响应函", f.matched_title)), None)
                pages = list(range(decl.page_start, (decl.page_end or decl.page_start) + 1))[:4] if decl else list(range(1, min(6, ctx.bdoc.n_pages) + 1))
                t3 = re.sub(r"\s+", "", "".join(ctx.bdoc.page_text(p) for p in pages))
                mm = re.search(r"有效期[^。]{0,40}?(\d{2,3})\s*(?:个)?(?:日历)?[日天]", t3)
                promised = bool(re.search(r"有效期内始终保持有效|按前附表[^。]{0,10}有效期|有效期内不撤销|有效期为", t3))
                days_ok = (int(mm.group(1)) >= (h.validity_days or 0)) if mm else None
                ok = promised and days_ok is not False
                out.append(self.make("pass" if ok else ("fail" if days_ok is False else "warning"), requirement=f"响应文件有效期不少于 {h.validity_days} 日历日",
                                     requirement_loc=h.sources.get("validity_days") or c.loc,
                                     actual=("响应声明已承诺有效期" + (f"（{mm.group(1)} 天）" if mm else "（按前附表）")) if promised else "响应声明未见有效期承诺",
                                     evidence=[ctx.bid_page_loc(pages[0], "附件1 磋商响应声明/响应函")],
                                     missing="" if ok else ("有效期短于要求" if days_ok is False else "需人工核对"), fix="" if ok else "在响应声明中承诺不少于要求天数"))
            elif n == "5":
                out.append(self.manual(c.text, "见 GC-T-02 偏离表与 ★ 条款逐项结果", [], c.loc))
            elif n == "6":
                out.append(self.manual(c.text, "需通读偏离表与承诺资料是否附加条件（大模型规则未启用时人工）", [], c.loc))
            elif n == "7":
                if h.control_price and L and L.total_price:
                    ok = L.total_price.value <= h.control_price.value
                    out.append(self.make("pass" if ok else "fail", requirement=f"报价不超过最高限价 {fmt_money(h.control_price)}", requirement_loc=h.sources.get("control_price"),
                                         actual=f"开标一览表响应报价 {fmt_money(L.total_price)}（{L.total_price.value / h.control_price.value * 100:.1f}%）",
                                         evidence=[L.loc] if L.loc else [], missing="" if ok else "报价超限价（情形7）", fix="" if ok else "下调报价"))
                else:
                    out.append(self.manual("报价不超过最高限价", "未抽到报价或限价", [], c.loc))
        return out


class GCT01StarTech(Rule):
    id, group, title, severity = "GC-T-01", "技术文件暗标", "第三章 ★实质性要求 1-4 响应", "reject"
    supersedes = ["mandatoryReq"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        stars = [c for c in ctx.req.rejection if c.id.startswith("★技术")]
        if not stars:
            return [self.na("未解析出★实质性要求")]
        for c in stars:
            t = c.text
            if "横道图" in t or "施工进度表" in t:
                g = _commit(ctx, "横道图进度表")
                s = _commit(ctx, "安全责任承诺")
                probs = []
                if not g:
                    probs.append("未见横道图形式施工进度表（未提供则无效响应）")
                if "安全责任" in t and not s:
                    probs.append("未见施工安全责任承诺（未承诺按无效响应）")
                out.append(self.make("fail" if probs else "pass", requirement=t[:300], requirement_loc=c.loc,
                                     actual="；".join(x for x in [f"横道图 p{g.loc.page}" if g else "", f"安全责任承诺 p{s.loc.page}" if s else ""] if x) or "—",
                                     evidence=[x.loc for x in (g, s) if x], missing="；".join(probs), fix="补充横道图进度表与安全责任承诺书"))
            elif "CCC" in t or "3C" in t or "强制性" in t:
                cert_loc = _find_in_bid(ctx, r"3C认证|CCC认证|强制性产品认证|节能产品认证", "认证证书")
                out.append(self.make("pass", requirement=t[:200], requirement_loc=c.loc, actual="响应文件已附相关认证证书", evidence=[cert_loc]) if cert_loc
                           else self.na("响应文件未见 3C/强制认证内容；若清单无此类产品则不适用，有则需附认证证书", c.loc))
            else:
                key = "环境" if "环境" in t else ("成品保护" if "成品" in t else t[:10])
                loc = None
                for p in range(1, ctx.bdoc.n_pages + 1):
                    if re.search(key + r"|噪音|噪声" if key == "环境" else key, ctx.bdoc.page_text(p)):
                        loc = ctx.bid_page_loc(p, "技术方案")
                        break
                out.append(self.make("pass" if loc else "warning", requirement=t[:300], requirement_loc=c.loc,
                                     actual=f"技术方案中出现「{key}」相关内容" if loc else f"未检索到「{key}」相关响应", evidence=[loc] if loc else [],
                                     missing="" if loc else "需在方案或承诺中明确响应", confidence=0.6))
        return out


class GCT02Deviation(Rule):
    id, group, title, severity = "GC-T-02", "技术文件暗标", "附件5-1/5-2 偏离表填写与负偏离", "reject"
    supersedes = ["compliance", "mandatoryReq"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        for key, label in (("偏离表5-1", "附件5-1 技术和服务要求响应表"), ("偏离表5-2", "附件5-2 商务条件和其它事项响应表")):
            s = ctx.idx.appendix.get(key)
            if not s:
                out.append(self.make("fail", requirement=f"须提交{label}", actual="未找到", missing="缺偏离表", fix="按附件5格式逐条填写"))
                continue
            m = re.search(r"rows=(\d+);negative=\[([\d, ]*)\]", str(s.value))
            rows = int(m.group(1)) if m else 0
            neg = [int(x) for x in m.group(2).split(",") if x.strip()] if m else []
            if neg:
                out.append(self.make("fail", requirement=f"{label} ★条款不得负偏离", actual=f"出现负偏离（p{', p'.join(map(str, neg))}）",
                                     evidence=[ctx.bid_page_loc(p, label) for p in neg[:3]], missing="★负偏离按无效响应处理", fix="改为完全响应"))
            elif rows == 0:
                out.append(self.make("warning", requirement=f"{label} 应逐条列出响应承诺", actual="表格仅有表头，未填写任何行",
                                     evidence=[s.loc], missing="按附件注(1)未书面提出偏离视为完全满足，但无法体现逐项响应，评审可能按不利认定",
                                     fix="逐条填入章节条目号、要求、响应承诺、无偏离"))
            else:
                out.append(self.make("pass", requirement=f"{label} 逐条响应且无负偏离", actual=f"{rows} 行，无负偏离", evidence=[s.loc]))
        return out


class GCT03Staffing(Rule):
    id, group, title, severity = "GC-T-03", "人员配备", "【项号11】项目团队：岗位数量 + 不兼岗承诺 + 本单位在岗 + 养老保险逐人", "major"
    method = "ocr"
    supersedes = ["qualification"]

    def _pension_pages(self, ctx: RuleContext) -> list[int]:
        """含「养老保险」文字的页及其后紧邻扫描页；按团队人数封顶 8 页。"""
        team = len({p.name for p in ctx.idx.personnel.people}) if ctx.idx.personnel else 0
        cap = max(2, min(8, team + 1))
        out: list[int] = []
        n = ctx.bdoc.n_pages
        for p in range(1, n + 1):
            if "养老保险" in ctx.bdoc.page_text(p):
                if ctx.bdoc.pages[p - 1].is_scanned or len(ctx.bdoc.page_text(p)) < 400:
                    out.append(p)
                q = p + 1
                while q <= n and ctx.bdoc.pages[q - 1].is_scanned and len(out) < cap:
                    out.append(q)
                    q += 1
            if len(out) >= cap:
                break
        return sorted(set(out))[:cap]

    def ocr_pages_needed(self, ctx: RuleContext) -> list[int]:
        return self._pension_pages(ctx)

    def _pension_finding(self, ctx: RuleContext, st) -> Finding:
        pages = self._pension_pages(ctx)
        texts = [ctx.ocr_text.get(p, "") for p in pages if ctx.ocr_text.get(p) and not ctx.ocr_text.get(p, "").startswith("[OCR失败]")]
        pen_text_layer = any("养老保险" in ctx.bdoc.page_text(p) for p in range(1, ctx.bdoc.n_pages + 1))
        ev = [ctx.bid_page_loc(p, "养老保险证明") for p in pages[:4]]
        req = "全部岗位人员提供截止前六个月内任一月养老保险证明"
        if not texts:
            return self.make("pass" if pen_text_layer else "warning", requirement=req, requirement_loc=st.source,
                             actual="已见养老保险证明相关页（未 OCR，逐人未核）" if pen_text_layer else "未见养老保险证明", evidence=ev,
                             missing="" if pen_text_layer else "缺养老保险证明", method="rule", confidence=0.6)
        joined = "\n".join(texts)
        team = {p.name for p in ctx.idx.personnel.people} if ctx.idx.personnel else set()
        missing_people = sorted(n for n in team if n not in joined)
        dl = parse_date(ctx.req.hard.deadline)
        months = months_covered(joined)
        win = [month_key(add_months(dl.replace(day=1), -i)) for i in range(1, 7)] if dl else []
        in_win = [m for m in months if m in win]
        problems = []
        if team and missing_people:
            problems.append(f"养老保险证明未见：{'、'.join(missing_people)}")
        if months and win and not in_win:
            problems.append(f"识别到的缴费月份 {', '.join(months[:6])} 不在截止前六个月（{win[-1]}~{win[0]}）内")
        actual = f"OCR {len(texts)} 页；识别到团队 {len(team) - len(missing_people)}/{len(team)} 人" + (f"；缴费月份 {', '.join(months[:6])}" if months else "；未读出缴费月份")
        return self.make("fail" if problems else "pass", requirement=req, requirement_loc=st.source, actual=actual, evidence=ev,
                         missing="；".join(problems), fix="" if not problems else "补充缺失人员的养老保险缴费证明", method="ocr", confidence=0.65)

    async def run(self, ctx: RuleContext) -> list[Finding]:
        st = ctx.req.staffing
        if not st:
            return [self.na("第三章未解析出人员要求")]
        ppl = ctx.idx.personnel
        ev = [ppl.loc] if ppl and ppl.loc else []
        req_txt = "；".join(f"{r} {v.count} 人" for r, v in st.roles.items())
        names = {}
        if ppl:
            for x in ppl.people:
                names.setdefault(x.post, []).append(x.name)
        out = [self.manual(req_txt, ("人员页 p%d-%d；识别到：" % (ppl.loc.page, ppl.loc.page_end or ppl.loc.page) + "；".join(f"{k}:{'/'.join(v[:3])}" for k, v in names.items())) if ppl else "未定位到人员页",
                           ev, st.source, fix="核对每岗人数、证书与养老保险证明")]
        if ppl:
            def _cnt(role: str) -> int:
                if role == "项目负责人":
                    return len(set(names.get("项目负责人", []) + names.get("项目经理", [])))
                if role == "项目技术负责人":
                    return len(set(names.get("技术负责人", [])))
                return len(set(names.get(role, [])))
            short = [f"{r} 需 {v.count} 人/见 {_cnt(r)} 人" for r, v in st.roles.items() if _cnt(r) < v.count]
            out.append(self.make("pass" if not short else "warning", requirement="各岗位人数不少于【项号11】要求", requirement_loc=st.source,
                                 actual="；".join(short) if short else "各岗位人数满足", evidence=ev,
                                 missing="" if not short else "文字层识别到的人员不足（可能在扫描页），需人工核对", confidence=0.6))
        for key, req in (("不兼岗承诺", "全部团队人员本项目内部岗位不得相互兼任（须单独承诺函）"),
                         ("本单位在岗承诺", "派驻人员必须为本单位在岗人员（须单独承诺函）")):
            c = _commit(ctx, key)
            out.append(self.make("pass" if c else "warning", requirement=req, requirement_loc=st.source,
                                 actual=f"已见 p{c.loc.page}" if c else "未检索到相应承诺", evidence=[c.loc] if c else [],
                                 missing="" if c else "缺单独承诺函（在响应表中响应不算）", fix="" if c else "补充单独承诺函"))
        out.append(self._pension_finding(ctx, st))
        return out


class GCB01StarBiz(Rule):
    id, group, title, severity = "GC-B-01", "商务初审否决", "三、商务条件 ★1-8 逐条 + 其他商务硬要求", "reject"
    supersedes = ["compliance", "consistency"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h, L = ctx.req.hard, ctx.idx.letter
        out: list[Finding] = []
        for c in [c for c in ctx.req.rejection if c.id.startswith("★商务")]:
            t = c.text
            if t.startswith("交货时间"):
                if L and L.duration_days and h.duration_days:
                    ok = L.duration_days <= h.duration_days
                    out.append(self.make("pass" if ok else "fail", requirement=t[:120], requirement_loc=c.loc, actual=f"开标一览表施工工期 {L.duration_days} 日",
                                         evidence=[L.loc] if L.loc else [], missing="" if ok else "工期超要求（★负偏离）", fix="" if ok else "改为不超过要求"))
                else:
                    out.append(self.manual(t[:120], "未抽到工期", [], c.loc))
            elif t.startswith("履约保证金"):
                want = pct(t)
                if want is None:
                    out.append(self.manual(t[:150], "招标未写明履约保证金比例，以附件5-2 逐条响应为准", [], c.loc))
                else:
                    pat = rf"履约保证金.{{0,40}}{want:g}\s*%|{want:g}\s*%.{{0,40}}履约保证金"
                    loc = _find_in_bid(ctx, pat, "商务响应")
                    out.append(self.make("pass" if loc else "warning", requirement=t[:150], requirement_loc=c.loc,
                                         actual=f"已见 {want:g}% 履约保证金响应" if loc else f"未检索到 {want:g}% 履约保证金的明确响应",
                                         evidence=[loc] if loc else [], missing="" if loc else "需在偏离表或承诺中明确响应", confidence=0.6))
            else:
                out.append(self.manual(t[:150], "以附件5-2 逐条响应为准（表为空时无法核对）", [], c.loc))
        # 其他商务硬点：招标文件里「须出具 XX 专项声明/保证函，未提供按废标/无效」的条款（解析层 parse_declarations 抽出，随文件变化）
        from services.fujian_check.bid.gov_cs import DECL_TOPICS

        covered: set[str] = set()
        decls = [c for c in ctx.req.rejection if c.id.startswith("专项声明-") or c.id == "前附表12-代理服务费保证函"]
        for c in decls:
            key = c.id.split("-", 1)[1] if c.id.startswith("专项声明-") else "代理服务费专项保证函"
            if key in covered or key in ("横道图进度表", "安全责任承诺", "不兼岗承诺", "本单位在岗承诺"):   # 由 GC-T-01 / GC-T-03 核对
                continue
            covered.add(key)
            found = _commit(ctx, key)
            if found is None and key not in ("团意险专项声明", "代理服务费专项保证函", "不分包转包承诺", "缺陷责任期承诺", "农民工工资承诺", "安全责任承诺", "横道图进度表", "不兼岗承诺", "本单位在岗承诺"):
                topic = key.replace("专项声明", "").replace("声明函", "").replace("保证函", "").replace("承诺函", "").replace("承诺书", "")
                loc = _find_in_bid(ctx, re.escape(topic) + r".{0,60}(声明|承诺|保证函)|(声明|承诺|保证函).{0,60}" + re.escape(topic), "专项声明") if topic else None
                found = Sourced(value=loc.excerpt, loc=loc) if loc else None
            hard = bool(re.search(r"废标|无效|不符合[^。]{0,15}实质性|资格审查不合格|否决", c.text))
            out.append(self.make("pass" if found else ("fail" if hard else "warning"), requirement=c.text[:220], requirement_loc=c.loc,
                                 actual=f"已见 p{found.loc.page}：{str(found.value)[:80]}" if found else "响应文件未检索到相应声明/保证函",
                                 evidence=[found.loc] if found else [], missing="" if found else ("缺该项专项声明/保证函，属实质性条款" if hard else "建议补充明确承诺"),
                                 fix="" if found else "按磋商文件要求补充相应声明函/保证函"))
        # 软性承诺：只在磋商文件提到时才查，要求原文取自磋商文件
        for kw, key in (("分包", "不分包转包承诺"), ("缺陷责任期", "缺陷责任期承诺"), ("农民工", "农民工工资承诺")):
            if key in covered:
                continue
            if kw == "分包":
                pol = h.subcontract_policy or ""
                if not re.search(r"不允许|不得|禁止", pol):
                    continue
                hit = (pol[:200], h.sources.get("subcontract_policy"))
            else:
                hit = _tender_sentence(ctx, kw)
            if not hit:
                continue
            sent, loc_req = hit
            found = _commit(ctx, key)
            out.append(self.make("pass" if found else "warning", requirement=sent[:200], requirement_loc=loc_req,
                                 actual=f"已见 p{found.loc.page}：{str(found.value)[:80]}" if found else "未检索到明确承诺",
                                 evidence=[found.loc] if found else [], missing="" if found else "建议在承诺函或偏离表中明确响应",
                                 fix="" if found else "补充相应承诺", confidence=0.6))
        return out or [self.na("磋商文件未解析出★商务条款或专项声明要求")]


class GCX01Consistency(Rule):
    id, group, title, severity = "GC-X-01", "交叉一致性", "供应商名称 / 项目经理 各处一致", "major"
    supersedes = ["consistency"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        L = ctx.idx.letter
        rx = re.compile(r"供应商(?:名称)?\s*[:：]\s*([一-龥（）()]{4,40}?(?:公司|集团))")
        seen: dict[str, Location] = {}
        for p in range(1, ctx.bdoc.n_pages + 1):
            for m in rx.finditer(to_halfwidth_safe(ctx.bdoc.page_text(p))):
                nm = norm(m.group(1))
                if nm.startswith("名称") or "公章" in nm:
                    continue
                seen.setdefault(nm, ctx.bid_page_loc(p, None, m.group(0)))
        if seen:
            out.append(self.make("pass" if len(seen) == 1 else "warning", requirement="各处供应商名称一致", actual="；".join(seen), evidence=list(seen.values())[:3],
                                 missing="" if len(seen) == 1 else "出现多个供应商名称写法"))
        # 签字代表 = 法定代表人，否则须有授权书
        legal = ctx.idx.legal_rep
        signer = L.signer if L else None
        if signer and legal and signer != legal:
            auth = next((f for f in ctx.idx.forms if f.page_start and re.search(r"授权", f.matched_title)), None)
            auth_loc = ctx.form_loc(auth) if auth else _find_in_bid(ctx, r"授权委托书|授权书|法定代表人授权", "授权书")
            out.append(self.make("pass" if auth_loc else "warning", requirement=f"响应文件签字代表 {signer} 非法定代表人 {legal}，须附法定代表人授权书",
                                 actual="已见授权书" if auth_loc else "未检索到授权书", evidence=[auth_loc] if auth_loc else ([L.loc] if L and L.loc else []),
                                 missing="" if auth_loc else "缺授权书或为扫描件，需人工核对", fix="" if auth_loc else "补充法定代表人授权书"))
        elif signer and legal:
            out.append(self.make("pass", requirement="响应文件由法定代表人签署", actual=f"签字代表 {signer} = 法定代表人", evidence=[L.loc] if L and L.loc else []))
        return out


def to_halfwidth_safe(s: str) -> str:
    from services.fujian_check.textnorm import to_halfwidth

    return to_halfwidth(s)


class GCS01Bonus(Rule):
    id, group, title, severity = "GC-S-01", "评分项自查", "商务/技术加分材料是否已提供（业绩、满意度、职称、特种作业证、样品）", "info"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        out: list[Finding] = []
        items = [(k, v) for k, v in ctx.req.appendix_params.items() if k.startswith("评分:") and "满分" not in k]
        if not items:
            return [self.na("未解析出评分项")]
        kw = {"业绩": r"中标.{0,4}通知书|成交通知书|采购合同|验收", "满意度": r"满意度评价", "人员情况1": r"职称证", "人员情况2": r"职称证",
              "人员情况3": r"特种作业操作证", "技术服务响应": None}
        for k, v in items:
            name = k[3:]
            pat = next((p for key, p in kw.items() if key in name), "")
            if pat is None or not pat:
                continue
            hits = [p for p in range(1, ctx.bdoc.n_pages + 1) if re.search(pat, ctx.bdoc.page_text(p))]
            out.append(self.make("pass" if hits else "warning", requirement=f"{name}：{str(v.value)[:120]}", requirement_loc=v.loc,
                                 actual=f"相关材料出现在 p{', p'.join(map(str, hits[:5]))}" if hits else "未见相关材料（不影响有效性，影响得分）",
                                 evidence=[ctx.bid_page_loc(p, "加分材料") for p in hits[:3]], missing="" if hits else "可能失分", method="rule", confidence=0.5))
        return out


RULES = [GCF01Attachments, GCQ01Materials, GCQ02Special, GCV01Invalid, GCT01StarTech, GCT02Deviation, GCT03Staffing, GCB01StarBiz,
         GCX01Consistency, GCS01Bonus]
