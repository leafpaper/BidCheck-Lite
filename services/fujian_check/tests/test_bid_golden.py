"""投标文件 C 索引黄金断言。"""
from __future__ import annotations


def _fr(idx, section, no):
    return next(f for f in idx.forms if f.form and f.form.section == section and f.form.no == no)


def test_pdf_reader_c(bid_c):
    assert bid_c.n_pages == 1020
    assert sum(1 for p in bid_c.pages if p.is_scanned) == 103


def test_sections_c(idx_c):
    assert idx_c.sections["资格文件"] == (2, 49)
    assert idx_c.sections["商务文件"] == (50, 843)
    assert idx_c.sections["技术文件"] == (844, 895)
    assert idx_c.sections["定标文件"] == (896, 1020)
    assert idx_c.bidder_name == "福建嘉圣景建工有限公司"
    assert idx_c.legal_rep == "张娟华"


def test_forms_c(idx_c):
    expect = {"一": 5, "三": 12, "六": 18, "七": 19, "九": 26, "十": 32, "十一": 33, "十二": 34,
              "十四": 36, "十六": 40, "十九": 43, "二十": 47}
    for no, page in expect.items():
        assert _fr(idx_c, "资格文件", no).page_start == page, no
    assert _fr(idx_c, "资格文件", "十").match_kind == "alias"
    assert _fr(idx_c, "资格文件", "四").match_kind == "scanned"
    assert _fr(idx_c, "资格文件", "五").match_kind == "scanned"
    assert _fr(idx_c, "资格文件", "二十一").match_kind == "missing"   # 到账证明真实缺失
    assert _fr(idx_c, "商务文件", "一").page_start == 53
    assert _fr(idx_c, "商务文件", "二").page_start == 54
    assert _fr(idx_c, "商务文件", "四").page_start == 60 and _fr(idx_c, "商务文件", "四").page_end == 842
    assert _fr(idx_c, "定标文件", "二").page_start == 899
    assert _fr(idx_c, "定标文件", "六").page_start == 920
    assert _fr(idx_c, "定标文件", "十一").page_start == 1011
    assert _fr(idx_c, "定标文件", "十二").page_start == 1017
    assert idx_c.boq_page_range == (60, 842)
    assert idx_c.scanned_attribution[27].startswith("拟派出项目技术负责人")


def test_letter_c(idx_c):
    L = idx_c.letter
    assert L.total_price.value == 31_708_739
    assert L.total_price_cn_value.value == 31_708_739
    assert L.duration_days == 330 and L.quota_duration_days == 372
    assert L.deposit.value == 340_000
    assert L.pm_name == "林正庆"
    assert L.validity_days is None
    assert L.loc.page == 53
    assert idx_c.boq_cover["total"] == "31708739"
    assert idx_c.boq_cover["total_cn"] == "叁仟壹佰柒拾万捌仟柒佰叁拾玖"
    assert idx_c.boq_summary["合计"].value == 31_708_739
    assert idx_c.boq_summary["合计_安全文明施工费"].value == 890_385
    assert idx_c.boq_summary["暂列金额"].value == 1_650_122
    assert "24个月" in idx_c.appendix["缺陷责任期"].value


def test_personnel_c(idx_c):
    ppl = idx_c.personnel.people
    posts = {p.post: p.name for p in ppl}
    assert posts["项目负责人"] == "林正庆"
    assert posts["机械员"] == "张娟华"
    assert posts["安全员"] == "林敏霞"
    pm = next(p for p in ppl if p.post == "项目负责人")
    assert pm.id_no == "350104197903241511"
    assert pm.cert_no == "闽1352008200903854"
    assert len([p for p in ppl if "施工员" in p.post]) == 2


def test_tech_segments_c(idx_c):
    segs = idx_c.tech_segments
    assert len(segs) == 7
    assert segs[0].page_start == 845
    dup = [s for s in segs if s.title.startswith("危大工程清单2")]
    assert len(dup) == 2 and any("钢结构" in s.title for s in dup)
    assert next(s for s in dup if "钢结构" in s.title).page_start == 856
