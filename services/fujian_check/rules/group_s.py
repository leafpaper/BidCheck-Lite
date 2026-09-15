"""FJ-S 施工现场管理人员配备（数据表 3.1.8）。"""
from __future__ import annotations

import re
from collections import Counter

from services.fujian_check.models import Finding
from services.fujian_check.rules.base import Rule, RuleContext

GROUP = "人员配备"

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


RULES = [S01Headcount, S02OnePersonOnePost, S04TechLeadTitle, S06TableVsAward]
