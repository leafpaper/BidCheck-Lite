"""政府采购竞争性磋商模板：举重中心磋商文件 ↔ 中闽谦合响应文件 黄金断言。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services.fujian_check.tests.conftest import GOLDEN_DIR

TENDER_CS = GOLDEN_DIR / "磋商" / "举重中心运动员楼综合修缮工程磋商文件（[350001]DHZB[CS]202600120260820002）.pdf"
BID_CS = GOLDEN_DIR / "磋商" / "举重中心.pdf"


@pytest.fixture(scope="session")
def req_cs():
    if not TENDER_CS.exists():
        pytest.skip("磋商黄金样本不存在")
    from services.fujian_check.pdf_reader import parse_pdf
    from services.fujian_check.profiles import detect_profile
    from services.fujian_check.tender.extract import extract_requirements

    doc = parse_pdf(str(TENDER_CS))
    return extract_requirements(doc, detect_profile(doc))


@pytest.fixture(scope="session")
def report_cs():
    if not (TENDER_CS.exists() and BID_CS.exists()):
        pytest.skip("磋商黄金样本不存在")
    from services.fujian_check.engine import run_check

    return asyncio.run(run_check(str(TENDER_CS), str(BID_CS), llm=None, use_llm=False, use_ocr=False))


def test_profile_and_hard(req_cs):
    assert req_cs.profile.key == "fujian_gov_cs" and req_cs.profile.confidence >= 0.8
    h = req_cs.hard
    assert h.control_price.value == 1_796_580
    assert h.deposit.value == 0
    assert h.validity_days == 90
    assert h.duration_days == 90
    assert h.joint_venture.allowed is False
    assert "贰级" in h.qualification and "安全生产许可证" in h.qualification
    assert "二级" in h.pm_requirement and "B" in h.pm_requirement
    assert req_cs.project_code == "[350001]DHZB[CS]2026001"


def test_structures(req_cs):
    assert len(req_cs.qianfubiao) == 13
    quals = [f for f in req_cs.forms_in("资格文件") if f.no.isdigit()]
    assert [f.no for f in quals] == [str(i) for i in range(1, 12)]
    ids = {c.id for c in req_cs.rejection}
    assert {f"14.2.1-{i}" for i in range(1, 8)} <= ids
    assert {f"★技术{i}" for i in range(1, 5)} <= ids
    assert {f"★商务{i}" for i in range(1, 9)} <= ids
    assert "前附表12-代理服务费保证函" in ids
    assert len(req_cs.datasheet) == 12
    st = req_cs.staffing
    assert st.roles["施工员"].count == 2 and st.roles["项目负责人"].count == 1 and st.roles["安全员"].cert == "C证"
    att = req_cs.forms_in("响应文件")
    assert {f.no for f in att} >= {"1", "2", "3", "3-4-1", "4", "5-1", "5-2", "6", "7", "7-1-1", "8"}
    assert next(f for f in att if f.no == "3-1").optional is True
    assert req_cs.appendix_params["评分:技术部分满分"].value == "75.0000"


def _v(report, rid):
    return [f.verdict for f in report.findings if f.rule_id == rid]


def test_report_cs(report_cs):
    r = report_cs
    assert r.has_critical_issues is True
    assert r.stats.elapsed_s < 120
    b = [f for f in r.findings if f.rule_id == "GC-B-01"]
    assert any("代理服务费" in f.requirement and f.verdict == "fail" for f in b)        # 真实缺失
    assert any("交货时间" in f.requirement and f.verdict == "pass" for f in b)          # 90 日 ≤ 90 日
    assert any("团意险" in f.requirement and f.verdict == "pass" for f in b)
    v = [f for f in r.findings if f.rule_id == "GC-V-01"]
    assert any("最高限价" in f.requirement and f.verdict == "pass" for f in v)          # 1,760,648 ≤ 1,796,580
    assert set(_v(r, "GC-T-02")) == {"warning"}                                          # 偏离表只有表头
    t1 = [f for f in r.findings if f.rule_id == "GC-T-01"]
    assert any("横道图" in f.requirement and f.verdict == "pass" for f in t1)
    q = [f for f in r.findings if f.rule_id == "GC-Q-01"]
    assert sum(1 for f in q if f.verdict == "pass") >= 8
    assert not any(f.verdict == "fail" for f in q)                                       # 授权书为扫描件 → warning 而非 fail
    f1 = [f for f in r.findings if f.rule_id == "GC-F-01"]
    assert not any(f.verdict == "fail" for f in f1)
    assert any(f.rule_id == "GC-T-03" and "技术负责人" in f.actual for f in r.findings)
