"""黄金样本 fixture：招标 A（福建理工大学宿舍，945 页）、招标 B（长乐污水管网，461 页）、投标 C（8.13.pdf，1020 页）。

文件不在时自动 skip。目录可用环境变量 FJ_GOLDEN_DIR 覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

GOLDEN_DIR = Path(os.environ.get("FJ_GOLDEN_DIR", r"F:/bid test/资料"))
TENDER_A = GOLDEN_DIR / "福建理工大学鼓山校区15号学生宿舍项目（施工）（评定分离）.pdf"
TENDER_B = GOLDEN_DIR / "向量库资料" / "郑和中路至爱心路段污水管网工程（施工）（评定分离）+招标文件(1).pdf"
BID_C = GOLDEN_DIR / "8.13.pdf"


def _need(path: Path):
    if not path.exists():
        pytest.skip(f"黄金样本不存在: {path}")


@pytest.fixture(scope="session")
def tender_a():
    _need(TENDER_A)
    from services.fujian_check.pdf_reader import parse_pdf

    return parse_pdf(str(TENDER_A))


@pytest.fixture(scope="session")
def tender_b():
    _need(TENDER_B)
    from services.fujian_check.pdf_reader import parse_pdf

    return parse_pdf(str(TENDER_B))


@pytest.fixture(scope="session")
def bid_c():
    _need(BID_C)
    from services.fujian_check.pdf_reader import parse_pdf

    return parse_pdf(str(BID_C))


@pytest.fixture(scope="session")
def req_a(tender_a):
    from services.fujian_check.profiles import detect_profile
    from services.fujian_check.tender.extract import extract_requirements

    return extract_requirements(tender_a, detect_profile(tender_a))


@pytest.fixture(scope="session")
def req_b(tender_b):
    from services.fujian_check.profiles import detect_profile
    from services.fujian_check.tender.extract import extract_requirements

    return extract_requirements(tender_b, detect_profile(tender_b))


@pytest.fixture(scope="session")
def idx_c(bid_c, req_a):
    from services.fujian_check.bid.index import build_bid_index
    from services.fujian_check.profiles import get_profile

    return build_bid_index(bid_c, req_a, get_profile(req_a.profile.key))
