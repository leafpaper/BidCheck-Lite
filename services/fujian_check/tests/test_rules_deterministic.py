"""确定性规则在黄金对 A↔C 上的预期判定（--no-llm --no-ocr）。"""
from __future__ import annotations

import asyncio

import pytest

from services.fujian_check.tests.conftest import BID_C, TENDER_A


@pytest.fixture(scope="session")
def report():
    if not (TENDER_A.exists() and BID_C.exists()):
        pytest.skip("黄金样本不存在")
    from services.fujian_check.engine import run_check

    return asyncio.run(run_check(str(TENDER_A), str(BID_C), llm=None, use_llm=False, use_ocr=False))


def _verdicts(report, rule_id: str) -> list[str]:
    return [f.verdict for f in report.findings if f.rule_id == rule_id]


def _one(report, rule_id: str):
    fs = [f for f in report.findings if f.rule_id == rule_id]
    assert fs, rule_id
    return fs[0]


def test_summary(report):
    assert report.profile.key == "fujian_platform"
    assert report.has_critical_issues is True        # T-02 危大清单粘贴残留 + 到账证明缺失
    assert report.risk_level == "high"
    assert report.stats.llm_calls == 0 and report.stats.ocr_pages == 0
    assert report.stats.elapsed_s < 60


def test_pass_rules(report):
    for rid in ("FJ-B-02", "FJ-B-05", "FJ-B-06", "FJ-B-08", "FJ-P-01", "FJ-P-02", "FJ-P-04", "FJ-P-05", "FJ-P-06",
                "FJ-P-07", "FJ-P-10", "FJ-X-01", "FJ-X-03", "FJ-X-05", "FJ-X-06", "FJ-T-01", "FJ-F-04", "FJ-F-05",
                "FJ-S-04", "FJ-D-05"):
        assert _verdicts(report, rid) == ["pass"], rid
    assert set(_verdicts(report, "FJ-X-02")) == {"pass"} and len(_verdicts(report, "FJ-X-02")) == 7
    assert set(_verdicts(report, "FJ-S-01")) == {"pass"} and len(_verdicts(report, "FJ-S-01")) == 7


def test_v02_rules_on_golden(report):
    """v0.2 新规则：签署日期、岗位证书、注册专业、人员表自检、异项目残留（全部文字层，0 token）。"""
    for rid in ("FJ-P-09", "FJ-S-03", "FJ-S-05", "FJ-X-07"):
        assert _verdicts(report, rid) == ["pass"], rid
    p09 = _one(report, "FJ-P-09")
    assert "2026-08-12" in p09.actual and "2026-08-13" in p09.requirement and "2026-07-17" in p09.requirement
    s07 = [f for f in report.findings if f.rule_id == "FJ-S-07"]
    assert len(s07) == 3 and all(f.verdict == "pass" for f in s07)
    p07 = _one(report, "FJ-P-07")
    assert "2027-01-26" in p07.actual                       # 简要情况表文字层的注册证书有效期
    c07 = _one(report, "FJ-C-07")
    assert c07.verdict == "manual" and c07.evidence         # 未开 OCR → 定位到扫描页待人工
    assert report.hard.notice_date == "2026-07-17" and report.hard.similar_projects_years == 5
    assert (report.hard.social_start_offset, report.hard.social_months) == (2, 6)


def test_known_defects(report):
    t02 = [f for f in report.findings if f.rule_id == "FJ-T-02"]
    fails = [f for f in t02 if f.verdict == "fail"]
    assert len(fails) == 1 and any(e.page == 856 for e in fails[0].evidence)
    assert len([f for f in t02 if f.verdict == "pass"]) == 6

    q07 = _one(report, "FJ-Q-07")
    assert q07.verdict == "warning" and q07.evidence[0].page == 18

    assert _verdicts(report, "FJ-T-04") == ["warning"]
    assert _verdicts(report, "FJ-P-03") == ["warning"]
    s02 = _verdicts(report, "FJ-S-02")
    assert s02[0] == "pass" and "warning" in s02          # 法定代表人兼机械员

    f01 = [f for f in report.findings if f.rule_id == "FJ-F-01"]
    assert f01[0].verdict == "fail"
    assert any("二十一" in f.requirement and f.verdict == "fail" for f in f01)       # 到账证明缺失
    assert any("四" in f.requirement and f.verdict == "warning" for f in f01)        # 诚信承诺函为扫描件


def test_na_rules(report):
    for rid in ("FJ-Q-06", "FJ-Q-09", "FJ-Q-12", "FJ-P-08"):
        assert _verdicts(report, rid) == ["na"], rid


def test_manual_rules_have_evidence(report):
    for f in report.findings:
        if f.rule_id.startswith("FJ-C-"):
            assert f.verdict == "manual" and f.evidence, f.rule_id


def test_platform_payload_shape(report):
    from services.fujian_check.report import to_platform_payload

    p = to_platform_payload(report)
    assert p["checks"] and all(set(c) >= {"check_name", "required", "actual", "status", "detail", "suggestion"} for c in p["checks"])
    assert set(p["summary"]) >= {"total", "passed", "failed", "warning"}
    assert p["risk_level"] == "high" and p["has_critical_issues"] is True
    assert all(c["status"] in ("pass", "fail", "warning") for c in p["checks"])
