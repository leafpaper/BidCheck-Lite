"""FJ-S 施工现场管理人员配备（数据表 3.1.8）。"""
from __future__ import annotations

import re
from collections import Counter

from services.fujian_check.bid.personnel import _MAJOR_RE
from services.fujian_check.dates import age_at, id_valid, iso, parse_date
from services.fujian_check.models import Finding
from services.fujian_check.rules.base import Rule, RuleContext

GROUP = "人员配备"
_POST_PREFIX_RE = re.compile(r"^(土建|设备安装|市政|装饰|水电|安装)")

_POST_FAMILY = {
    "施工员": ("施工员",), "质量员": ("质量员",), "材料员": ("材料员",), "机械员": ("机械员",),
    "安全员": ("安全员",), "劳务员": ("劳务员",), "资料员": ("资料员",), "标准员": ("标准员",),
    "测量员": ("测量员",), "试验员": ("试验员",), "项目负责人": ("项目负责人",), "项目技术负责人": ("技术负责人",),
}


def _count(people, role: str) -> list:
    keys = _POST_FAMILY.get(role, (role,))
    return [p for p in people if any(k in p.post for k in keys) and not (role == "项目负责人" and "技术" in p.post)]


class S01Headcount(Rule):
    id, group, title, severity = "FJ-S-01", GROUP, "各岗位人数 ≥ 数据表 3.1.8 最低要求", "reject"
    supersedes = ["qualification"]
    method = "table"
    source_clause = "3.1.8"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        st = ctx.req.staffing
        if not st or not st.roles:
            return [self.manual("人员岗位与数量", "招标数据表未解析出人员要求", [], ctx.clause_loc("3.1.8"))]
        ppl = ctx.idx.personnel.people if ctx.idx.personnel else []
        fr = ctx.form("施工现场管理人员表") or ctx.form("施工管理人员表")
        ev = [ctx.form_loc(fr)] if fr and fr.page_start else []
        if not ppl:
            return [self.make("fail", requirement="；".join(f"{r} {v.count} 人" for r, v in st.roles.items()),
                              requirement_loc=st.source, actual="未解析出人员表", evidence=ev,
                              missing="缺《拟派出施工现场管理人员表》", fix="在福建省住建政务系统生成人员表并上传")]
        out: list[Finding] = []
        for role, need in st.roles.items():
            if need.count == 0:
                continue
            got = _count(ppl, role)
            ok = len(got) >= need.count
            out.append(self.make("pass" if ok else "fail", requirement=f"{role} ≥ {need.count} 人" + (f"（{need.cert}）" if need.cert else ""),
                                 requirement_loc=st.source, actual=f"{len(got)} 人：" + "、".join(p.name for p in got),
                                 evidence=ev, missing="" if ok else f"{role}人数不足（3.1.8 资格否决）",
                                 fix="" if ok else "补足人员并重新生成人员表"))
        return out


class S02OnePersonOnePost(Rule):
    id, group, title, severity = "FJ-S-02", GROUP, "一人一职，不得兼任；法定代表人兼岗提示", "reject"
    method = "table"
    source_clause = "3.1.8"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        st = ctx.req.staffing
        ppl = ctx.idx.personnel.people if ctx.idx.personnel else []
        if not ppl:
            return [self.na("无人员表")]
        fr = ctx.form("施工现场管理人员表") or ctx.form("施工管理人员表")
        ev = [ctx.form_loc(fr)] if fr and fr.page_start else []
        names = Counter(p.name for p in ppl)
        ids = Counter(p.id_no for p in ppl if p.id_no)
        dup = [n for n, c in names.items() if c > 1] + [i for i, c in ids.items() if c > 1]
        out: list[Finding] = []
        if dup:
            out.append(self.make("fail", requirement="施工现场管理各岗位人员必须一人一职，不得兼任", requirement_loc=st.source if st else None,
                                 actual="重复出现：" + "、".join(dup), evidence=ev, missing="同一人担任多个岗位", fix="调整人员，一人一岗"))
        else:
            out.append(self.make("pass", requirement="一人一职，不得兼任", requirement_loc=st.source if st else None,
                                 actual=f"{len(ppl)} 人无重复", evidence=ev))
        legal = ctx.idx.legal_rep
        if legal:
            hit = [p for p in ppl if p.name == legal]
            if hit:
                out.append(self.make("warning", requirement="法定代表人担任现场管理岗位需确认为本企业在岗且不影响一人一职",
                                     actual=f"法定代表人 {legal} 兼任 {hit[0].post}", evidence=ev,
                                     missing="评标委员会可能质疑其到岗履职", fix="建议改派专职人员"))
        return out


class S04TechLeadTitle(Rule):
    id, group, title, severity = "FJ-S-04", GROUP, "项目技术负责人职称达标（中级及以上）", "reject"
    source_clause = "3.1.8"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        st = ctx.req.staffing
        need = st.roles.get("项目技术负责人") if st else None
        if not need:
            return [self.na("数据表无技术负责人职称要求")]
        ppl = ctx.idx.personnel.people if ctx.idx.personnel else []
        tl = next((p for p in ppl if "技术负责人" in p.post), None)
        fr = ctx.form("技术负责人简要情况表")
        ev = [ctx.form_loc(fr)] if fr and fr.page_start else []
        if tl is None:
            return [self.make("fail", requirement=f"项目技术负责人 {need.cert or ''}", requirement_loc=st.source, actual="未找到技术负责人",
                              evidence=ev, missing="缺项目技术负责人", fix="补充表单九")]
        title = tl.title or tl.cert or ""
        rank = 3 if "高级" in title else (2 if re.search(r"(?<!助理)(?<!高级)工程师|中级", title) else (1 if "助理" in title or "初级" in title else None))
        need_rank = 2 if need.cert and "中级" in need.cert else (3 if need.cert and "高级" in need.cert else 2)
        if rank is None:
            return [self.manual(f"项目技术负责人 {need.cert}", f"{tl.name}：职称文字层未识别（{title}）", ev, st.source)]
        ok = rank >= need_rank
        return [self.make("pass" if ok else "fail", requirement=f"项目技术负责人 {need.cert}", requirement_loc=st.source,
                          actual=f"{tl.name}：{title}", evidence=ev, missing="" if ok else "职称等级不足", fix="" if ok else "更换符合职称要求的人员")]


class S06TableVsAward(Rule):
    id, group, title, severity = "FJ-S-06", GROUP, "资格文件人员表 ↔ 定标文件人员表 姓名一致", "major"
    method = "table"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        award = next((f for f in ctx.idx.forms if f.form and f.form.section == "定标文件" and "管理人员表" in f.form.title and f.page_start), None)
        if not award or not ctx.idx.personnel:
            return [self.na("无定标文件人员表或资格人员表")]
        from services.fujian_check.bid.personnel import _parse_staff_table_text

        award_people = _parse_staff_table_text(ctx.bdoc, award.page_start, award.page_end or award.page_start)
        a = {p.name for p in ctx.idx.personnel.people}
        b = {p.name for p in award_people}
        if not b:
            return [self.manual("两份人员表姓名一致", "定标文件人员表未解析出人员", [ctx.form_loc(award)])]
        extra = b - a        # 定标表里出现、资格表没有的人 → 可疑
        only_q = a - b       # 定标表文字层未出现（可能在扫描页）→ 仅提示
        ok = not extra
        actual = f"资格 {len(a)} 人 / 定标 {len(b)} 人"
        if extra:
            actual += "；定标新增：" + "、".join(sorted(extra))
        if only_q:
            actual += "；定标表文字层未见：" + "、".join(sorted(only_q)) + "（可能在扫描页）"
        return [self.make("pass" if ok else "warning", requirement="定标文件人员表应与资格文件人员表一致", actual=actual,
                          evidence=[ctx.form_loc(award)], missing="" if ok else "定标文件出现资格文件未列的人员", fix="" if ok else "统一人员")]


class S03PostCertMatch(Rule):
    id, group, title, severity = "FJ-S-03", GROUP, "施工现场管理人员岗位证书类型与岗位一致，安全员持 C 证", "reject"
    method = "table"
    source_clause = "3.1.8"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        st = ctx.req.staffing
        ppl = [p for p in (ctx.idx.personnel.people if ctx.idx.personnel else []) if "负责人" not in p.post]
        if not ppl:
            return [self.na("无施工现场管理人员表")]
        fr = ctx.form("施工现场管理人员表") or ctx.form("施工管理人员表")
        ev = [ctx.form_loc(fr)] if fr and fr.page_start else []
        problems: list[str] = []
        no_number: list[str] = []
        for p in ppl:
            base = _POST_PREFIX_RE.sub("", p.post)
            cert = p.cert or ""
            if base == "安全员":
                ok = bool(re.search(r"C", cert.upper())) or "安全" in cert
            else:
                ok = base[:2] in cert
            if not ok:
                problems.append(f"{p.post} {p.name}：岗位证书「{cert or '空'}」")
            if not p.cert_no:
                no_number.append(f"{p.post} {p.name}")
        req = "各岗位人员须持对应岗位证书（安全员为安全生产考核合格证 C 证）"
        if problems:
            return [self.make("fail", requirement=req, requirement_loc=st.source if st else None, actual="；".join(problems),
                              evidence=ev, missing="岗位证书类型与岗位不符（3.1.8 资格否决）", fix="更换持有对应岗位证书的人员并重新生成人员表")]
        actual = f"{len(ppl)} 人证书类型均与岗位一致"
        if no_number:
            return [self.make("warning", requirement=req, requirement_loc=st.source if st else None, actual=actual + "；证书编号未识别：" + "、".join(no_number),
                              evidence=ev, missing="部分人员证书编号为空或未识别", fix="核对人员表证书编号列", confidence=0.7)]
        return [self.make("pass", requirement=req, requirement_loc=st.source if st else None, actual=actual, evidence=ev)]


class S05PmRegisteredMajor(Rule):
    id, group, title, severity = "FJ-S-05", GROUP, "项目负责人建造师注册专业与招标要求专业一致", "reject"
    source_clause = "3.1.8"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        h = ctx.req.hard
        req_loc = h.sources.get("pm_requirement") or ctx.qfb_loc("4.1")
        m = _MAJOR_RE.search(h.pm_requirement or "")
        if not m:
            return [self.na("招标未限定建造师注册专业", req_loc)]
        need = m.group(1)
        pm = next((p for p in (ctx.idx.personnel.people if ctx.idx.personnel else []) if p.post == "项目负责人"), None)
        if pm is None:
            return [self.manual(f"项目负责人须为{need}专业注册建造师", "未抽到项目负责人", [], req_loc)]
        ev = [ctx.bid_page_loc(pm.page, "拟派出施工现场管理人员表")] if pm.page else []
        if not pm.reg_major:
            return [self.manual(f"项目负责人须为{need}专业注册建造师", f"{pm.name}：注册专业未能识别", ev, req_loc)]
        ok = need in pm.reg_major or pm.reg_major in need
        return [self.make("pass" if ok else "fail", requirement=f"项目负责人须为{need}专业注册建造师", requirement_loc=req_loc,
                          actual=f"{pm.name}：注册专业 {pm.reg_major}", evidence=ev,
                          missing="" if ok else "建造师注册专业与招标要求不符（3.1.8 资格否决）", fix="" if ok else "更换对应专业的注册建造师")]


class S07StaffTableSelfCheck(Rule):
    id, group, title, severity = "FJ-S-07", GROUP, "人员表查询有效期/项目编号 + 身份证校验位与年龄", "major"
    method = "table"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        pt = ctx.idx.personnel
        dl = parse_date(ctx.req.hard.deadline)
        out: list[Finding] = []
        if pt and pt.loc:
            ev = [pt.loc]
            if pt.query_valid_end:
                end = parse_date(pt.query_valid_end)
                ok = not (dl and end and end < dl)
                out.append(self.make("pass" if ok else "fail", requirement=f"人员表查询有效期须覆盖投标截止 {iso(dl)}", actual=f"查询有效期至 {pt.query_valid_end}",
                                     evidence=ev, missing="" if ok else "人员表已过查询有效期", fix="" if ok else "重新生成人员表"))
            if pt.project_code and ctx.req.project_code:
                ok = pt.project_code == ctx.req.project_code or pt.project_code.startswith(ctx.req.project_code[:15])
                out.append(self.make("pass" if ok else "fail", requirement=f"人员表招标项目编号 = {ctx.req.project_code}", actual=f"人员表编号 {pt.project_code}",
                                     evidence=ev, missing="" if ok else "人员表属于其他项目", fix="" if ok else "按本项目重新生成人员表"))
        # 身份证
        subjects = [(p.post, p.name, p.id_no, p.page) for p in (pt.people if pt else []) if p.id_no]
        if ctx.idx.legal_rep and ctx.idx.legal_rep_id:
            fr = ctx.form("法定代表人")
            subjects.append(("法定代表人", ctx.idx.legal_rep, ctx.idx.legal_rep_id, fr.page_start if fr else None))
        if subjects:
            bad: list[str] = []
            old: list[str] = []
            ok_n = 0
            ev2 = []
            for post, name, idn, page in subjects:
                if page:
                    ev2.append(ctx.bid_page_loc(page, post))
                v = id_valid(idn)
                if v is False:
                    bad.append(f"{post} {name}：{idn}")
                    continue
                ok_n += 1
                age = age_at(idn, dl)
                if age is not None and age >= 60:
                    old.append(f"{post} {name} {age} 岁")
            if bad:
                out.append(self.make("warning", requirement="身份证号校验位正确", actual="校验位错误：" + "；".join(bad), evidence=ev2[:4],
                                     missing="身份证号校验位错误（录入有误），评标可能视为信息不实", fix="核对原件改正"))
            if old:
                out.append(self.make("warning", requirement="拟派人员未达退休年龄（截止日 60 岁）", actual="；".join(old), evidence=ev2[:4],
                                     missing="已达退休年龄，评标可能质疑到岗履职", fix="确认返聘/在岗证明或更换人员"))
            if not bad and not old:
                out.append(self.make("pass", requirement="身份证号校验位正确且未达退休年龄", actual=f"{ok_n} 个身份证号校验通过", evidence=ev2[:4]))
        return out or [self.na("无人员表元数据与身份证号")]


RULES = [S01Headcount, S02OnePersonOnePost, S03PostCertMatch, S04TechLeadTitle, S05PmRegisteredMajor, S06TableVsAward, S07StaffTableSelfCheck]
