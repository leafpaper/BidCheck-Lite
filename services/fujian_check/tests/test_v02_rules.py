"""v0.2 新增规则的合成样本测试（不依赖黄金 PDF、不联网）：签署日期、岗位证书、注册专业、人员表自检、异项目残留、
授权委托书/身份证、社保逐人、保证金三要素、业绩时间窗、养老保险逐人。"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from services.fujian_check.dates import (
    age_at,
    find_dates,
    find_invalid_dates,
    id_valid,
    month_window,
    months_covered,
    parse_date,
)
from services.fujian_check.models import (
    BidIndex,
    BidLetter,
    Block,
    FormRange,
    Location,
    Page,
    ParsedDoc,
    Person,
    PersonnelTable,
    RequiredForm,
    RunStats,
    Sourced,
    StaffingRequirement,
    TenderProfile,
    TenderRequirements,
)
from services.fujian_check.rules.base import RuleContext

VALID_ID = "11010519491231002X"     # GB 11643 示例号码


def _doc(pages: list[str], scanned: set[int] = frozenset()) -> ParsedDoc:
    return ParsedDoc(path="x.pdf", file_hash="f" * 64, kind="pdf", parser_version="1", n_pages=len(pages),
                     pages=[Page(page=i + 1, char_count=len(t), is_scanned=(i + 1) in scanned,
                                 blocks=[Block(order=i, text=t)]) for i, t in enumerate(pages)])


def _ctx(pages: list[str] | None = None, scanned: set[int] = frozenset(), *, deadline="2026-08-13 09:10:00",
         notice="2026-07-17", validity=120, profile="fujian_platform") -> RuleContext:
    req = TenderRequirements(profile=TenderProfile(key=profile, confidence=1.0))
    req.hard.deadline = deadline
    req.hard.notice_date = notice
    req.hard.validity_days = validity
    req.project_code = "E3501000102802427001"
    req.project_name = "福建理工大学鼓山校区15号学生宿舍项目(施工)(评定分离)"
    idx = BidIndex()
    idx.bidder_name = "福建嘉圣景建工有限公司"
    idx.legal_rep = "张娟华"
    doc = _doc(pages or ["x"], scanned)
    return RuleContext(req=req, idx=idx, tdoc=doc, bdoc=doc, llm=None, use_llm=False, use_ocr=False, stats=RunStats())


def _run(rule, ctx):
    return asyncio.run(rule.run(ctx))


def _form(title: str, p0: int, p1: int | None = None, scanned: list[int] | None = None, kind="exact", section="资格文件") -> FormRange:
    return FormRange(form=RequiredForm(section=section, no="一", title=title), matched_title=title, page_start=p0,
                     page_end=p1 or p0, scanned_pages=scanned or [], match_kind=kind)


# ---------- dates ----------

def test_dates_helpers():
    assert parse_date("2026-08-13 09:10:00") == date(2026, 8, 13)
    assert parse_date("2026 年8 月12 日") == date(2026, 8, 12)
    assert parse_date("2026.7.30-2027.1.26") == date(2026, 7, 30)
    assert find_dates("有效期 2026.7.30-2027.1.26") == ["2026-07-30", "2027-01-26"]
    assert find_invalid_dates("日期:2026年2月30日") == ["2026年2月30日"]
    assert month_window(date(2026, 8, 13), 2, 6) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    assert months_covered("2026年1月至2026年3月缴费") == ["2026-01", "2026-03", "2026-02"] or set(months_covered("2026年1月至2026年3月缴费")) == {"2026-01", "2026-02", "2026-03"}
    assert id_valid(VALID_ID) is True and id_valid("11010519491231002") is None and id_valid("110105194912310021") is False
    assert age_at(VALID_ID, date(2026, 8, 13)) == 76


# ---------- P-09 签署日期 ----------

def test_p09_sign_dates():
    from services.fujian_check.rules.group_p import P09SignDates

    ctx = _ctx(["投标函 ... 2026-08-12", "承诺函 日 期:2026 年8 月20 日"])
    ctx.idx.form_dates = {"投标函": Sourced(value="2026-08-12", loc=Location(doc="bid", page=1)),
                          "承诺函": Sourced(value="2026-08-20", loc=Location(doc="bid", page=2))}
    f = _run(P09SignDates(), ctx)[0]
    assert f.verdict == "fail" and "承诺函" in f.missing and "晚于" in f.missing

    ctx = _ctx(["投标函 2026-08-12", "承诺函 2026-08-12"])
    ctx.idx.form_dates = {"投标函": Sourced(value="2026-08-12", loc=Location(doc="bid", page=1)),
                          "承诺函": Sourced(value="2026-08-12", loc=Location(doc="bid", page=2))}
    assert _run(P09SignDates(), ctx)[0].verdict == "pass"

    ctx = _ctx(["投标函 2026-07-01"])
    ctx.idx.form_dates = {"投标函": Sourced(value="2026-07-01", loc=Location(doc="bid", page=1))}
    f = _run(P09SignDates(), ctx)[0]
    assert f.verdict == "fail" and "早于招标文件日期" in f.missing

    ctx = _ctx(["承诺函 日期:2026年2月30日 另 2026-08-12"])
    ctx.idx.form_dates = {"承诺函": Sourced(value="2026-08-12", loc=Location(doc="bid", page=1))}
    f = _run(P09SignDates(), ctx)[0]
    assert f.verdict == "fail" and "非法日期" in f.missing

    ctx = _ctx(["x"])
    assert _run(P09SignDates(), ctx)[0].verdict == "manual"


# ---------- S-03 / S-05 / S-07 ----------

def _people():
    return [Person(name="林正庆", post="项目负责人", cert="注册建造师(一级)", cert_no="闽1352008", reg_major="建筑工程", page=1),
            Person(name="林敏霞", post="安全员", cert="C类人员", cert_no="闽建安C3(2024)1", page=1),
            Person(name="杨洪", post="土建施工员", cert="土建施工员", cert_no="0352", page=1),
            Person(name="陈灯豪", post="土建质量员", cert="土建质量员", cert_no="0352", page=1)]


def test_s03_post_cert_match():
    from services.fujian_check.rules.group_s import S03PostCertMatch

    ctx = _ctx()
    ctx.idx.personnel = PersonnelTable(people=_people(), loc=Location(doc="bid", page=1))
    assert _run(S03PostCertMatch(), ctx)[0].verdict == "pass"
    ctx.idx.personnel.people[1].cert = "材料员"          # 安全员拿材料员证
    f = _run(S03PostCertMatch(), ctx)[0]
    assert f.verdict == "fail" and "安全员 林敏霞" in f.actual
    ctx.idx.personnel.people[1].cert = "C类人员"
    ctx.idx.personnel.people[2].cert_no = None
    f = _run(S03PostCertMatch(), ctx)[0]
    assert f.verdict == "warning" and "杨洪" in f.actual


def test_s05_registered_major():
    from services.fujian_check.rules.group_s import S05PmRegisteredMajor

    ctx = _ctx()
    ctx.req.hard.pm_requirement = "项目负责人须具备有效的不低于贰级建筑工程专业注册建造师执业资格"
    ctx.idx.personnel = PersonnelTable(people=_people(), loc=Location(doc="bid", page=1))
    assert _run(S05PmRegisteredMajor(), ctx)[0].verdict == "pass"
    ctx.idx.personnel.people[0].reg_major = "市政公用工程"
    assert _run(S05PmRegisteredMajor(), ctx)[0].verdict == "fail"
    ctx.idx.personnel.people[0].reg_major = None
    assert _run(S05PmRegisteredMajor(), ctx)[0].verdict == "manual"
    ctx.req.hard.pm_requirement = "项目负责人须具备贰级注册建造师"
    assert _run(S05PmRegisteredMajor(), ctx)[0].verdict == "na"


def test_s07_staff_table_self_check():
    from services.fujian_check.rules.group_s import S07StaffTableSelfCheck

    ctx = _ctx()
    ctx.idx.personnel = PersonnelTable(people=_people(), loc=Location(doc="bid", page=1), query_valid_end="2027-02-08",
                                       project_code="E3501000102802427001")
    ctx.idx.personnel.people[0].id_no = VALID_ID
    ctx.idx.legal_rep_id = "110105194912310021"     # 校验位错
    out = _run(S07StaffTableSelfCheck(), ctx)
    v = {f.requirement[:6]: f.verdict for f in out}
    assert any(f.verdict == "pass" and "查询有效期" in f.requirement for f in out)
    assert any(f.verdict == "pass" and "招标项目编号" in f.requirement for f in out)
    assert any(f.verdict == "warning" and "校验位" in f.missing for f in out)
    assert any(f.verdict == "warning" and "退休" in f.missing and "林正庆" in f.actual for f in out)

    ctx.idx.personnel.query_valid_end = "2026-08-01"
    ctx.idx.personnel.project_code = "E3502000199999999001"
    out = _run(S07StaffTableSelfCheck(), ctx)
    assert any(f.verdict == "fail" and "过查询有效期" in f.missing for f in out)
    assert any(f.verdict == "fail" and "其他项目" in f.missing for f in out)


# ---------- X-07 异项目残留 ----------

def test_x07_foreign_residue():
    from services.fujian_check.rules.group_x import X07ForeignProjectResidue

    ctx = _ctx(["招标项目编号 E3501000102802427001 标段 E3501000102802427001001", "本页正常 E350100010280242700（换行截断）"])
    assert _run(X07ForeignProjectResidue(), ctx)[0].verdict == "pass"
    ctx = _ctx(["招标项目编号 E3501000102802427001", "危大工程清单 招标项目编号 E3502000199999999001 招标项目名称:长乐污水管网工程"])
    f = _run(X07ForeignProjectResidue(), ctx)[0]
    assert f.verdict == "warning" and "E3502000199999999001" in f.actual and f.evidence[0].page == 2


# ---------- C-07 授权委托书 / 身份证 ----------

def test_c07_authorization_and_id():
    from services.fujian_check.rules.group_c import C07AuthorizationAndId

    ctx = _ctx(["三、法定代表人资格证明书 姓名:张娟华", "扫描页"], scanned={2})
    ctx.idx.forms = [_form("法定代表人资格证明书", 1, 2, scanned=[2])]
    ctx.idx.letter = BidLetter(signer="张娟华")
    r = C07AuthorizationAndId()
    assert r.ocr_pages_needed(ctx) == [2]
    assert _run(r, ctx)[0].verdict == "manual"                     # 未 OCR
    ctx.ocr_text[2] = "身份证|居民身份证|350121198606085710|闽清县公安局|2020-06-08至2040-06-08|张娟华|||"
    assert _run(r, ctx)[0].verdict == "pass"
    ctx.ocr_text[2] = "身份证|居民身份证|350121198606085710|闽清县公安局|2016-06-08至2026-06-08|张娟华|||"
    f = _run(r, ctx)[0]
    assert f.verdict == "fail" and "身份证有效期止 2026-06-08" in f.missing
    # 委托代理人签署但无授权委托书
    ctx.ocr_text[2] = "身份证|居民身份证|350121198606085710|闽清县公安局|长期|张娟华|||"
    ctx.idx.letter = BidLetter(signer="李四")
    f = _run(r, ctx)[0]
    assert f.verdict == "fail" and "未见授权委托书" in f.missing
    ctx.ocr_text[2] += "\n授权委托书|授权委托书||福建嘉圣景建工有限公司|2026-08-01至2026-09-30|李四|2026-08-10||委托人张娟华 被委托人李四"
    f = _run(r, ctx)[0]
    assert f.verdict == "fail" and "授权有效期止 2026-09-30" in f.missing
    ctx.ocr_text[2] = ctx.ocr_text[2].replace("2026-08-01至2026-09-30", "2026-08-01至2027-08-01")
    assert _run(r, ctx)[0].verdict == "pass"


# ---------- C-05 社保 / C-06 保证金 ----------

def test_social_and_deposit_checks():
    from services.fujian_check.rules.group_c import deposit_checks, social_checks

    ctx = _ctx()
    ctx.idx.personnel = PersonnelTable(people=_people())
    ctx.req.hard.social_start_offset, ctx.req.hard.social_months = 2, 6
    ok = "社保缴费证明|福建嘉圣景建工有限公司|||||林敏霞||2026-01至2026-06|养老保险"
    p, w = social_checks(ctx, ok)
    assert p == [] and w == []
    p, w = social_checks(ctx, "社保缴费证明|福建嘉圣景建工有限公司||||王五||2026-03至2026-06|")
    assert any("未见安全员姓名" in x for x in p) and any("覆盖 4/6" in x for x in w)
    p, w = social_checks(ctx, "营业执照|xx|||||")
    assert p == [] and w == ["OCR 未识别到社保缴费证明"]

    ctx.idx.basic_account = "13185101040022355"
    p, w = deposit_checks(ctx, "电汇凭证|中国农业银行||||付款账号 13185101040022355 E3501000102802427001 340000||2026-08-10||", [])
    assert p == [] and w == []
    p, w = deposit_checks(ctx, "电汇凭证|中国农业银行||||付款账号 62220000000000001||2026-08-10||", [])
    assert any("基本账户" in x for x in w) and any("招标编号" in x for x in w)
    p, w = deposit_checks(ctx, "保函|投标保函|BH001|担保公司|2026-08-01至2026-12-01|福建嘉圣景建工有限公司|||E3501000102802427001", [])
    assert any("保函有效期止 2026-12-01" in x for x in p)      # 需 ≥ 2026-08-13 + 120 + 30 = 2027-01-10


# ---------- Q-09 业绩时间窗（表格走缓存，不读 PDF） ----------

def test_q09_performance_window(monkeypatch, tmp_path):
    from services.fujian_check import cache
    from services.fujian_check.rules.group_q import Q09SimilarProjects

    monkeypatch.setenv("FJ_CACHE_DIR", str(tmp_path))
    ctx = _ctx(["“类似工程业绩”情况汇总表 表格页"])
    ctx.idx.forms = [_form("“类似工程业绩”情况汇总表", 1)]
    ctx.req.hard.similar_projects_required = 2
    ctx.req.hard.similar_projects_years = 5
    cache.tables_cache_put(ctx.bdoc.file_hash, 1, [[
        ["序号", "项目名称", "合同金额", "竣工验收日期"],
        ["1", "福建省图书馆改扩建工程", "16623.82", "2022.12.15"],
        ["2", "某市政道路工程", "5000", "2019-06-30"],       # 早于 2021-07-17
        ["3", "某学校宿舍楼", "8000", "2024-03-01"],
    ]])
    f = _run(Q09SimilarProjects(), ctx)[0]
    assert f.verdict == "pass" and "时间窗内 2 条" in f.actual
    ctx.req.hard.similar_projects_required = 3
    f = _run(Q09SimilarProjects(), ctx)[0]
    assert f.verdict == "fail" and "2 < 3" in f.missing


# ---------- GC-T-03 养老保险逐人 ----------

def test_gc_t03_pension_per_person():
    from services.fujian_check.rules.group_gc import GCT03Staffing

    ctx = _ctx(["项目团队 项目负责人 姓名:王一 养老保险证明见后", "扫描页", "扫描页"], scanned={2, 3}, deadline="2026-09-05", profile="fujian_gov_cs")
    ctx.req.staffing = StaffingRequirement(source=Location(doc="tender", page=9))
    ctx.idx.personnel = PersonnelTable(people=[Person(name="王一", post="项目负责人", page=1), Person(name="李二", post="安全员", page=1)],
                                       loc=Location(doc="bid", page=1))
    r = GCT03Staffing()
    assert r.ocr_pages_needed(ctx) == [1, 2, 3]          # 提及页（短文字层）+ 紧邻扫描页，封顶 团队人数+1
    ctx.ocr_text[2] = "社保缴费证明|养老保险|||||王一||2026-07|"
    ctx.ocr_text[3] = "社保缴费证明|养老保险|||||李二||2026-06|"
    fs = _run(r, ctx)
    pen = [f for f in fs if "养老保险" in f.requirement][0]
    assert pen.verdict == "pass" and "2/2 人" in pen.actual
    ctx.ocr_text[3] = "社保缴费证明|养老保险|||||赵六||2025-01|"
    pen = [f for f in _run(r, ctx) if "养老保险" in f.requirement][0]
    assert pen.verdict == "fail" and "李二" in pen.missing


# ---------- 磋商模板：专项声明从招标文件解析，不写死 ----------

def test_gc_declarations_parsed_from_tender():
    from services.fujian_check.tender.gov_cs import parse_declarations
    from services.fujian_check.rules.group_gc import GCB01StarBiz

    tender = _doc(["四、其他事项 1.成交供应商须在合同签订后为工人购买团体意外伤害保险，供应商应对此作出专项声明，未提供声明函按废标处理。"
                   "2.供应商须提供农民工工资支付承诺书，未提供的按无效响应处理。3.本项目不收取代理服务费。"])
    decls = parse_declarations(tender, [1], "第三章")
    ids = {c.id for c in decls}
    assert ids == {"专项声明-团意险专项声明", "专项声明-农民工工资承诺"}
    assert all("未提供" in c.text for c in decls)

    ctx = _ctx(["响应文件 我方承诺为本项目工人购买团体意外伤害保险。"], profile="fujian_gov_cs")
    ctx.tdoc = tender
    ctx.req.rejection = decls
    from services.fujian_check.bid.gov_cs import find_commitments
    for k, v in find_commitments(ctx.bdoc).items():
        ctx.idx.appendix[f"承诺:{k}"] = v
    fs = _run(GCB01StarBiz(), ctx)
    by = {f.requirement[:12]: f for f in fs}
    assert any(f.verdict == "pass" and "团体意外" in f.requirement for f in fs)
    assert any(f.verdict == "fail" and "农民工" in f.requirement for f in fs)
    assert not any("代理服务费" in f.requirement for f in fs)         # 招标未要求 → 不检查

    ctx2 = _ctx(["x"], profile="fujian_gov_cs")
    ctx2.tdoc = _doc(["无任何商务条款"])
    assert _run(GCB01StarBiz(), ctx2)[0].verdict == "na"
