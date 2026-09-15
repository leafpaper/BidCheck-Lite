"""llm_judge 的引用校验与降级逻辑（用 FakeLLM，不联网）。"""
from __future__ import annotations

import asyncio

from services.fujian_check.models import BidIndex, EvidenceWindow, Location, RunStats, TenderProfile, TenderRequirements
from services.fujian_check.rules.base import Rule, RuleContext
from services.fujian_check.rules.llm_judge import FakeLLM, judge_snippet


class _R(Rule):
    id, group, title = "FJ-TEST", "测试", "测试规则"


def _ctx(llm):
    req = TenderRequirements(profile=TenderProfile(key="fujian_platform", confidence=1.0))
    return RuleContext(req=req, idx=BidIndex(), tdoc=None, bdoc=None, llm=llm, use_llm=llm is not None, stats=RunStats())  # type: ignore[arg-type]


def _win(text: str):
    return [EvidenceWindow(loc=Location(doc="bid", page=53), text=text, score=1.0)]


def test_quote_must_be_substring():
    llm = FakeLLM([{"verdict": "fail", "quote": "片段里根本没有这句话", "reason": "x", "confidence": 0.9}])
    jr = asyncio.run(judge_snippet(_ctx(llm), _R(), "要求", _win("投标函：工期 330 日历天"), question="q"))
    assert jr.verdict == "manual"


def test_quote_ok_pass():
    llm = FakeLLM([{"verdict": "pass", "quote": "工期 330 日历天", "reason": "一致", "confidence": 0.95}])
    ctx = _ctx(llm)
    jr = asyncio.run(judge_snippet(ctx, _R(), "工期330", _win("投标函：工期 330 日历天"), question="q"))
    assert jr.verdict == "pass" and jr.confidence == 0.95
    assert ctx.stats.llm_calls == 1
    assert "投标文件片段" in llm.calls[0][1]["content"]


def test_no_llm_is_manual():
    jr = asyncio.run(judge_snippet(_ctx(None), _R(), "要求", _win("x"), question="q"))
    assert jr.verdict == "manual"


def test_budget_exhausted():
    llm = FakeLLM([{"verdict": "pass", "quote": "", "reason": ""}])
    ctx = _ctx(llm)
    ctx.llm_budget = 0
    jr = asyncio.run(judge_snippet(ctx, _R(), "要求", _win("x"), question="q"))
    assert jr.verdict == "manual" and ctx.stats.degraded_rules
