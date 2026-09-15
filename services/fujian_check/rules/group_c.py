"""FJ-C 扫描件证书：OCR 读证书要素，核对有效期覆盖投标截止日、单位名称与投标人一致、等级达标。

未启用 OCR 时只定位候选页并标 manual。
"""
from __future__ import annotations

import re

from services.fujian_check.dates import days_after, iso, month_window, months_covered, parse_date
from services.fujian_check.models import Finding, FormRange
from services.fujian_check.rules._helpers import level_rank, norm
from services.fujian_check.rules.base import Rule, RuleContext
from services.fujian_check.rules.ocr import dates_in, field, is_unreadable, lines_of_type, sign_date_of

GROUP = "扫描件证书"

# (id, 标题, 定位表单关键字, 招标条款, OCR 行关键词, 是否核对单位名称)
_CERTS = [
    ("FJ-C-01", "营业执照有效且单位名称=投标人", "投标人基本情况表", "3.1.4", ("营业执照", "统一社会信用代码"), True),
    ("FJ-C-02", "建筑业企业资质证书等级达标且有效", "投标人基本情况表", "3.1.5", ("资质证书", "资质等级", "总承包"), True),
    ("FJ-C-03", "安全生产许可证有效", "投标人基本情况表", "3.1.5", ("安全生产许可证", "安许"), True),
    ("FJ-C-04", "项目负责人建造师注册证书与安B证有效、注册单位=投标人", "项目负责人简要情况表", "3.1.8", ("建造师", "注册证书", "安全生产考核", "B证"), True),
    ("FJ-C-05", "安全员 C 证有效且附社保凭证", "安全员及其社保凭证", "3.1.8", ("安全生产考核", "C证", "社保", "社会保险"), False),
    ("FJ-C-06", "保证金到账/保函日期不晚于投标截止时间", "投标保证金", "3.1.3", ("电汇", "转账", "保函", "回单", "凭证"), False),
]


def _iso(s: str) -> str:
    y, m, d = re.split(r"[.\-/]", s)[:3]
    return f"{y}-{int(m):02d}-{int(d):02d}"


def _validity_ends(line: str) -> list[str]:
    """OCR 行 '类型|名称|编号|机关|有效期(起-止)|持有人|其他' → 明确的止日期或'长期'；起止不清/仅一个日期不作判定。"""
    fields = [f.strip() for f in line.split("|")]
    seg = fields[4] if len(fields) >= 5 else ""
    if not seg:
        return []
    if re.search(r"长期|永久", seg):
        return ["长期"]
    if "不清" in seg:
        return []
    ds = dates_in(seg)
    if len(ds) >= 2:
        return [max(ds)]
    return []


def _deadline_date(s: str | None) -> str | None:
    if not s:
        return None
    ds = dates_in(s)
    return ds[0] if ds else None


class CertRule(Rule):
    group = GROUP
    severity = "reject"
    method = "ocr"
    supersedes = ["qualification", "validity"]

    def __init__(self, rid: str, title: str, form_key: str, clause: str, kws: tuple[str, ...], check_unit: bool):
        self.id, self.title, self.form_key, self.source_clause, self.kws, self.check_unit = rid, title, form_key, clause, kws, check_unit

    def _pages(self, ctx: RuleContext) -> tuple[FormRange | None, list[int]]:
        fr = ctx.form(self.form_key)
        if not fr or not fr.page_start:
            return None, []
        pages = fr.scanned_pages or list(range(fr.page_start, (fr.page_end or fr.page_start) + 1))
        return fr, pages[:8]

    def ocr_pages_needed(self, ctx: RuleContext) -> list[int]:
        return self._pages(ctx)[1]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        fr, pages = self._pages(ctx)
        req_loc = ctx.clause_loc(self.source_clause)
        deadline = _deadline_date(ctx.req.hard.deadline)
        if not fr:
            return [self.make("warning", requirement=self.title, requirement_loc=req_loc, actual="未定位到对应表单",
                              missing="无法定位证书扫描件", fix="确认表单已提交", method="manual", confidence=0.5)]
        title = fr.form.title if fr.form else fr.matched_title
        ev = [ctx.bid_page_loc(p, title) for p in pages[:4]]
        texts = {p: ctx.ocr_text.get(p, "") for p in pages if ctx.ocr_text.get(p)}
        if not texts:
            return [self.manual(f"{self.title}（截止 {deadline or '投标截止日'}）", f"证书为扫描件 p{pages[0]}-{pages[-1]}，未启用 OCR",
                                ev, req_loc, fix="开启视觉 OCR 或人工翻页核对")]
        joined = "\n".join(texts.values())
        if all(is_unreadable(t) for t in texts.values()):
            return [self.make("warning", requirement=self.title, requirement_loc=req_loc, actual="OCR 无法辨认扫描件",
                              evidence=ev, missing="字迹模糊可能触发 3.1.2 否决", fix="重新扫描清晰件", confidence=0.6)]
        rel = lines_of_type(joined, *self.kws)
        # 文字层兜底：表单页本身（如项目负责人简要情况表）常已写明证书号与"使用有效期"
        layer = "\n".join(ctx.bdoc.page_text(p) for p in range(fr.page_start, (fr.page_end or fr.page_start) + 1))
        layer_lines = [ln for ln in layer.split("\n") if any(k in ln for k in self.kws) or "有效期" in ln]
        if not rel and not layer_lines:
            return [self.manual(self.title, f"OCR 未识别到相关证书行；OCR 摘要：{joined[:200]}", ev, req_loc)]
        problems: list[str] = []
        # 有效期：OCR 结构化行第 5 列（起-止）；只有明确的"止"日期才用于判定
        ends: list[str] = []
        for ln in rel:
            ends.extend(_validity_ends(ln))
        for m in re.finditer(r"有效期[^\d]{0,20}(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})\s*[-~—至到]\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})", layer):
            ends.append(_iso(m.group(2)))
        if deadline and ends:
            bad = [e for e in ends if e != "长期" and e < deadline]
            if bad:
                problems.append(f"有效期止 {min(bad)} 早于投标截止 {deadline}")
        if not rel:
            rel = layer_lines
        # 单位名称
        if self.check_unit and ctx.idx.bidder_name:
            units = [ln for ln in rel if norm(ctx.idx.bidder_name) in norm(ln)]
            if not units and any(re.search(r"公司|集团", ln) for ln in rel):
                problems.append("证书上的单位名称未见与投标人一致")
        # 资质等级（C-02）
        if self.id == "FJ-C-02":
            need = level_rank(ctx.req.hard.qualification)
            got = [level_rank(ln) for ln in rel]
            got = [g for g in got if g is not None]
            if need is not None and got and min(got) > need:
                problems.append("资质等级低于招标要求")
        # 截止日期（C-06）
        warns: list[str] = []
        if self.id == "FJ-C-06" and deadline:
            ds = [d for d in dates_in(joined)]
            late = [d for d in ds if d > deadline]
            if ds and late and not [d for d in ds if d <= deadline]:
                problems.append(f"凭证日期 {late[0]} 晚于投标截止")
            p2, w2 = deposit_checks(ctx, joined, rel)
            problems += p2
            warns += w2
        # 社保逐人（C-05）
        if self.id == "FJ-C-05":
            p2, w2 = social_checks(ctx, joined)
            problems += p2
            warns += w2
            if not ends and lines_of_type(joined, "社保", "社会保险", "养老", "参保"):
                ends = ["社保"]     # 社保证明本身无有效期，不因此降级
        actual = "OCR：" + "；".join(r[:80] for r in rel[:4])
        if problems:
            return [self.make("fail", requirement=f"{self.title}（截止 {deadline or '—'}）", requirement_loc=req_loc, actual=actual,
                              evidence=ev, missing="；".join(problems + warns), fix="更换/续期证书或核对单位名称", confidence=0.7)]
        if warns:
            return [self.make("warning", requirement=f"{self.title}（截止 {deadline or '—'}）", requirement_loc=req_loc, actual=actual,
                              evidence=ev, missing="；".join(warns), fix="按提示核对扫描件", confidence=0.7)]
        conf_note = "" if ends else "（未识别到有效期，需人工复核）"
        return [self.make("pass" if ends else "warning", requirement=f"{self.title}（截止 {deadline or '—'}）", requirement_loc=req_loc,
                          actual=actual + conf_note, evidence=ev, missing="" if ends else "OCR 未读出有效期", confidence=0.75)]


def social_checks(ctx: RuleContext, joined: str) -> tuple[list[str], list[str]]:
    """安全员社保证明：人名 = 人员表安全员、单位 = 投标人、缴费月份覆盖要求区间。返回 (problems, warnings)。"""
    problems: list[str] = []
    warns: list[str] = []
    lines = lines_of_type(joined, "社保", "社会保险", "养老", "参保", "缴费")
    if not lines:
        return problems, ["OCR 未识别到社保缴费证明"]
    text = "\n".join(lines)
    names = [p.name for p in (ctx.idx.personnel.people if ctx.idx.personnel else []) if "安全员" in p.post]
    if names and not any(n in text for n in names):
        problems.append(f"社保证明未见安全员姓名（人员表：{'、'.join(names)}）")
    bidder = ctx.idx.bidder_name
    if bidder and re.search(r"公司|集团", text) and norm(bidder) not in norm(text) and norm(bidder)[:6] not in norm(text):
        warns.append("社保证明缴费单位未见与投标人一致（可能为上级统筹单位）")
    dl = parse_date(ctx.req.hard.deadline)
    months = months_covered(text)
    if dl and months:
        off = ctx.req.hard.social_start_offset or 2
        need = ctx.req.hard.social_months or 6
        win = month_window(dl, off, need)
        have = [m for m in win if m in months]
        if len(have) < need:
            warns.append(f"缴费月份覆盖 {len(have)}/{need} 个月（要求 {win[0]} ~ {win[-1]}；识别到 {', '.join(months[:8])}）")
    elif dl and not months:
        warns.append("OCR 未读出缴费月份，需人工核对社保区间")
    return problems, warns


def deposit_checks(ctx: RuleContext, joined: str, rel: list[str]) -> tuple[list[str], list[str]]:
    """保证金凭证：付款账号 = 基本账户、注明招标项目编号、保函有效期 ≥ 截止 + 投标有效期 + 30 天。"""
    problems: list[str] = []
    warns: list[str] = []
    flat = re.sub(r"\s+", "", joined)
    acct = ctx.idx.basic_account
    if acct:
        nums = re.findall(r"\d{10,25}", flat)
        if nums and acct not in nums and not any(n.endswith(acct[-8:]) for n in nums):
            warns.append(f"凭证付款账号未见基本账户 {acct[:4]}…{acct[-4:]}（须从基本账户转出）")
    code = ctx.req.project_code
    if code and code[:15] not in flat:
        warns.append("凭证/保函未见注明本项目招标编号")
    dl = parse_date(ctx.req.hard.deadline)
    guarantee = lines_of_type(joined, "保函", "担保")
    if guarantee and dl:
        need_end = days_after(dl, (ctx.req.hard.validity_days or 0) + 30)
        ends = []
        for ln in guarantee:
            ends += [e for e in _validity_ends(ln) if e != "长期"]
        short = [e for e in ends if parse_date(e) and parse_date(e) < need_end]
        if short:
            problems.append(f"保函有效期止 {min(short)} 早于投标截止+有效期+30天（{iso(need_end)}）")
    return problems, warns


class C07AuthorizationAndId(Rule):
    id, group, title, severity = "FJ-C-07", GROUP, "授权委托书有效并覆盖评标期；法定代表人/委托代理人身份证在有效期", "reject"
    method = "ocr"
    supersedes = ["signature", "validity"]
    source_clause = "3.1.10"

    def _pages(self, ctx: RuleContext) -> list[int]:
        pages: list[int] = []
        for key in ("法定代表人资格证明书", "法定代表人身份证明", "授权委托书"):
            fr = ctx.form(key)
            if fr and fr.page_start:
                pages += fr.scanned_pages or ([] if fr.match_kind == "missing" else list(range(fr.page_start, (fr.page_end or fr.page_start) + 1)))
        return sorted(set(pages))[:6]

    def ocr_pages_needed(self, ctx: RuleContext) -> list[int]:
        return self._pages(ctx)

    async def run(self, ctx: RuleContext) -> list[Finding]:
        L = ctx.idx.letter
        legal = ctx.idx.legal_rep
        signer = (L.signer if L and L.signer else None) or legal
        need_auth = bool(signer and legal and signer != legal)
        dl = parse_date(ctx.req.hard.deadline)
        req_loc = ctx.clause_loc(self.source_clause)
        pages = self._pages(ctx)
        req = ("投标函由委托代理人签署，须附有效授权委托书（委托人=法定代表人、被委托人=签字人、有效期覆盖评标期）" if need_auth
               else "投标函由法定代表人签署，无需授权委托书") + "；身份证须在有效期内"
        ev = [ctx.bid_page_loc(p, "法定代表人资格证明/授权委托书") for p in pages[:4]]
        texts = {p: ctx.ocr_text.get(p, "") for p in pages if ctx.ocr_text.get(p)}
        if not pages:
            return [self.manual(req, "未定位到法定代表人资格证明书/授权委托书扫描页", [], req_loc)]
        if not texts:
            return [self.manual(req, f"扫描件 p{pages[0]}-{pages[-1]} 未 OCR", ev, req_loc, fix="开启视觉 OCR 或人工翻页核对")]
        joined = "\n".join(texts.values())
        problems: list[str] = []
        warns: list[str] = []
        notes: list[str] = [f"签字人 {signer or '—'}，法定代表人 {legal or '—'}"]
        # 身份证
        id_lines = lines_of_type(joined, "身份证")
        if id_lines:
            ends = []
            for ln in id_lines:
                ends += _validity_ends(ln)
            if dl and ends:
                bad = [e for e in ends if e != "长期" and parse_date(e) and parse_date(e) < dl]
                if bad:
                    problems.append(f"身份证有效期止 {min(bad)} 早于投标截止 {iso(dl)}")
                else:
                    notes.append("身份证有效期已核对")
            else:
                warns.append("身份证有效期未读出")
        else:
            warns.append("OCR 未识别到身份证")
        # 授权委托书
        if need_auth:
            auth = lines_of_type(joined, "授权委托书", "委托书", "授权书")
            if not auth:
                problems.append("未见授权委托书（签字人非法定代表人）")
            else:
                at = "\n".join(auth)
                if signer and signer not in at:
                    problems.append(f"授权委托书未见被委托人 {signer}")
                if legal and legal not in at:
                    warns.append(f"授权委托书未见委托人（法定代表人 {legal}）")
                sd = next((sign_date_of(ln) for ln in auth if sign_date_of(ln)), None)
                if sd and dl and parse_date(sd) and parse_date(sd) > dl:
                    problems.append(f"授权委托书签发日期 {sd} 晚于投标截止")
                ends = []
                for ln in auth:
                    ends += _validity_ends(ln)
                if dl and ends:
                    need_end = days_after(dl, ctx.req.hard.validity_days or 0)
                    short = [e for e in ends if e != "长期" and parse_date(e) and parse_date(e) < need_end]
                    if short:
                        problems.append(f"授权有效期止 {min(short)} 未覆盖投标有效期（至 {iso(need_end)}）")
                    else:
                        notes.append("授权有效期已核对")
                elif not re.search(r"本项目|本次投标|全过程|至.{0,6}(结束|完成|终止)", at):
                    warns.append("授权委托书有效期未读出（建议写明起止或至本项目结束）")
        actual = "；".join(notes) + "；OCR：" + "；".join(l[:60] for l in (lines_of_type(joined, "身份证", "委托") or [joined[:80]])[:3])
        if problems:
            return [self.make("fail", requirement=req, requirement_loc=req_loc, actual=actual, evidence=ev, missing="；".join(problems + warns),
                              fix="更换有效身份证扫描件 / 按第8章格式重新出具授权委托书", confidence=0.7)]
        if warns:
            return [self.make("warning", requirement=req, requirement_loc=req_loc, actual=actual, evidence=ev, missing="；".join(warns),
                              fix="人工核对扫描件", confidence=0.6)]
        return [self.make("pass", requirement=req, requirement_loc=req_loc, actual=actual, evidence=ev, confidence=0.75)]


RULES = [CertRule(*c) for c in _CERTS] + [C07AuthorizationAndId]
