"""run_check：解析 → profile → 需求 → 索引 → 规则（R/T 同步 → OCR 批量 → LLM 并发）→ 报告。"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Awaitable, Callable

from services.fujian_check import cache
from services.fujian_check.bid.index import build_bid_index
from services.fujian_check.models import CheckReport, Finding, ParsedDoc, RunStats
from services.fujian_check.pdf_reader import file_sha256, parse_pdf
from services.fujian_check.profiles import detect_profile, get_profile
from services.fujian_check.report import build_report
from services.fujian_check.rules import load_rules
from services.fujian_check.rules.base import Rule, RuleContext
from services.fujian_check.tender.extract import extract_requirements

ProgressFn = Callable[[float, str], Awaitable[None] | None]


async def _emit(progress: ProgressFn | None, p: float, msg: str) -> None:
    if progress is None:
        return
    r = progress(p, msg)
    if asyncio.iscoroutine(r):
        await r


def load_document(path: str, use_cache: bool = True, stats: RunStats | None = None) -> ParsedDoc:
    p = Path(path)
    if p.suffix.lower() == ".docx":
        from services.fujian_check.docx_reader import parse_docx

        return parse_docx(str(p))
    if use_cache:
        cached = cache.load_parsed(file_sha256(p))
        if cached is not None and cached.n_pages > 0:
            if stats:
                stats.cache_hits += 1
            return cached
    doc = parse_pdf(str(p))
    if use_cache:
        cache.save_parsed(doc)
    return doc


async def run_check(tender_path: str, bid_path: str, *, llm=None, use_llm: bool = True, use_ocr: bool = True,
                    use_cache: bool = True, rule_ids: list[str] | None = None,
                    progress: ProgressFn | None = None, ocr_budget: int = 40, llm_budget: int = 16) -> CheckReport:
    t0 = time.time()
    stats = RunStats()
    await _emit(progress, 0.05, "解析招标文件")
    tdoc = load_document(tender_path, use_cache, stats)
    await _emit(progress, 0.15, "识别模板")
    profile = detect_profile(tdoc)
    prof = get_profile(profile.key)
    await _emit(progress, 0.2, "抽取招标要求")
    req = extract_requirements(tdoc, profile)
    stats.parse_warnings += [f"招标: {w}" for w in req.parse_warnings]
    await _emit(progress, 0.35, "解析投标文件")
    bdoc = load_document(bid_path, use_cache, stats)
    await _emit(progress, 0.5, "建立投标索引")
    idx = build_bid_index(bdoc, req, prof)
    stats.parse_warnings += [f"投标: {w}" for w in idx.parse_warnings]

    ctx = RuleContext(req=req, idx=idx, tdoc=tdoc, bdoc=bdoc, llm=llm if use_llm else None, use_llm=use_llm and llm is not None,
                      use_ocr=use_ocr and bdoc.kind == "pdf" and bool(os.environ.get("ALIYUN_API_KEY")),
                      stats=stats, ocr_budget=ocr_budget, llm_budget=llm_budget)
    rules = load_rules(profile.key, rule_ids)
    sync_rules = [r for r in rules if r.method in ("rule", "table", "manual")]
    ocr_rules = [r for r in rules if r.method == "ocr"]
    llm_rules = [r for r in rules if r.method == "llm"]

    findings: list[Finding] = []
    await _emit(progress, 0.6, f"确定性规则 {len(sync_rules)} 条")
    for r in sync_rules:
        findings += await _safe_run(r, ctx)

    if ocr_rules:
        await _emit(progress, 0.72, f"扫描件 OCR（{len(ocr_rules)} 条规则）")
        if ctx.use_ocr:
            from services.fujian_check.rules.ocr import prefill_ocr

            await prefill_ocr(ctx, ocr_rules)
        for r in ocr_rules:
            findings += await _safe_run(r, ctx)

    if llm_rules:
        await _emit(progress, 0.85, f"大模型窗口判断（{len(llm_rules)} 条规则）")
        sem = asyncio.Semaphore(4)

        async def _one(r: Rule):
            async with sem:
                return await _safe_run(r, ctx)

        results = await asyncio.gather(*[_one(r) for r in llm_rules])
        for res in results:
            findings += res

    await _emit(progress, 0.95, "生成报告")
    stats.elapsed_s = round(time.time() - t0, 1)
    meta = {
        "tender": {"name": Path(tender_path).name, "hash": tdoc.file_hash[:16], "pages": tdoc.n_pages, "kind": tdoc.kind},
        "bid": {"name": Path(bid_path).name, "hash": bdoc.file_hash[:16], "pages": bdoc.n_pages, "kind": bdoc.kind},
    }
    report = build_report(findings, req, idx, meta, stats)
    await _emit(progress, 1.0, "完成")
    return report


async def _safe_run(rule: Rule, ctx: RuleContext) -> list[Finding]:
    try:
        return await rule.run(ctx)
    except Exception as e:  # 单条规则异常不阻断整体
        ctx.stats.parse_warnings.append(f"规则 {rule.id} 异常: {type(e).__name__}: {str(e)[:120]}")
        return [rule.make("manual", requirement=rule.title, actual=f"规则执行异常：{type(e).__name__}",
                          missing="引擎内部错误，需人工核对", fix="请反馈给维护者", method="manual", confidence=0.0)]
