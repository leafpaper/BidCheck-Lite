"""招标文件 A/B 解析黄金断言。"""
from __future__ import annotations

from services.fujian_check.tender.sections import find_section


def test_pdf_reader_a(tender_a):
    assert tender_a.n_pages == 945
    assert tender_a.pages[4].footer_stripped == "5 / 945"
    assert tender_a.pages[27].is_toc is True
    assert sum(1 for p in tender_a.pages if p.is_toc) <= 3


def test_profile_a(req_a):
    assert req_a.profile.key == "fujian_platform"
    assert req_a.profile.confidence >= 0.8
    assert req_a.profile.variant.get("评定分离") is True


def test_sections_a(req_a):
    secs = req_a.sections
    assert find_section(secs, 2).page_start == 5
    assert find_section(secs, 3).page_start == 53
    assert find_section(secs, 4).page_start == 68
    assert find_section(secs, 7).page_start == 840
    assert find_section(secs, 8).page_start == 858
    s21 = find_section(secs, 2, 1)
    assert (s21.page_start, s21.page_end) == (5, 25)


def test_qianfubiao_a(req_a):
    rows = req_a.qianfubiao
    assert len(rows) == 40
    assert [r.item_no for r in rows] == list(range(1, 41))
    assert "330" in req_a.qfb("2.1").content and "372" in req_a.qfb("2.1").content
    assert "120" in req_a.qfb("17.1").content
    assert "后审" in req_a.qfb("3.1").content
    assert req_a.qfb("4.1") is not None  # 条款号 "4.1 /4.2"


def test_hard_values_a(req_a):
    h = req_a.hard
    assert h.control_price.value == 34_652_556
    assert h.duration_days == 330 and h.quota_duration_days == 372
    assert h.validity_days == 120
    assert h.deposit.value == 340_000
    assert h.similar_projects_required == 0
    assert h.credit_score_applied is False
    assert h.joint_venture is not None and h.joint_venture.allowed is True
    assert h.ca_own_only is True and h.xml_required is True
    assert h.deadline.startswith("2026-08-13")
    assert "贰级" in h.pm_requirement and "B证" in h.pm_requirement
    assert h.sources["deposit"].page in (9, 10)
    assert req_a.project_code == "E3501000102802427001"


def test_rejection_a(req_a):
    rj = req_a.rejection
    q = [c for c in rj if c.group == "资格文件"]
    b = [c for c in rj if c.group == "商务初审" and c.id.startswith("5.1")]
    d = [c for c in rj if c.group == "详细评审"]
    assert [c.id for c in q] == [f"3.1.{i}" for i in range(1, 17)]
    assert all(c.loc.page in (62, 63) for c in q)
    assert [c.id for c in b] == [f"5.1.{i}" for i in range(1, 11)]
    assert all(c.loc.page == 63 for c in b)
    assert [c.id for c in d] == [f"7.1.{i}" for i in range(1, 7)]
    assert all(c.loc.page == 64 for c in d)
    assert any(c.id == "6.2.2" for c in rj)
    c318 = next(c for c in q if c.id == "3.1.8")
    assert "数据表第5项" in c318.refs


def test_datasheet_a(req_a):
    ds = req_a.datasheet
    assert len(ds) == 10
    assert [d.clause for d in ds][:5] == ["2.1", "2.2", "2.2", "2.3", "3.1.8"]
    st = req_a.staffing
    assert st.roles["施工员"].count == 2
    for r in ("质量员", "材料员", "机械员", "安全员", "劳务员"):
        assert st.roles[r].count == 1
    assert st.roles["安全员"].cert == "C证"
    assert st.roles["项目技术负责人"].cert == "中级职称"
    assert st.one_person_one_post is True
    assert st.source.page == 56
    assert len(req_a.dangerous_works) == 6
    assert "混凝土模板支撑" in req_a.dangerous_works[1].value


def test_forms_a(req_a):
    q = req_a.forms_in("资格文件")
    assert len(q) == 21
    assert [f.no for f in q][:3] == ["一", "二", "三"] and q[-1].no == "二十一"
    assert {f.no for f in q if f.optional} == {"二", "五", "六", "十三"}
    assert next(f for f in q if f.no == "十六").loc.page == 881
    assert len(req_a.forms_in("商务文件")) == 5
    assert len(req_a.forms_in("技术文件")) == 0
    assert len(req_a.forms_in("定标文件")) == 13


def test_appendix_a(req_a):
    ap = req_a.appendix_params
    assert "24个月" in ap["缺陷责任期"].value
    assert "10%" in ap["承包人履约担保金额"].value
    assert len(ap) == 7


def test_tender_b_generalizes(req_b):
    assert req_b.profile.key == "fujian_platform"
    assert len(req_b.qianfubiao) >= 30
    for c in ("2.1", "17.1", "18.1"):
        assert req_b.qfb(c) is not None, c
    assert req_b.hard.duration_days and req_b.hard.validity_days and req_b.hard.deposit
    assert req_b.hard.control_price is not None
    assert len([c for c in req_b.rejection if c.group == "资格文件"]) >= 14
    assert len(req_b.forms_in("商务文件")) >= 4
    assert req_b.parse_warnings == []
