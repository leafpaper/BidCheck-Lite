"""命令行入口（不依赖平台数据库）。

    python -m services.fujian_check.cli parse-tender --file A.pdf [--out req.json]
    python -m services.fujian_check.cli parse-bid --tender A.pdf --file C.pdf [--out index.json]
    python -m services.fujian_check.cli check --tender A.pdf --bid C.pdf [--out report.json] [--md report.md]
                                              [--no-llm] [--no-ocr] [--no-cache] [--rules FJ-X-01,FJ-T-02]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path


def _load(path: str, no_cache: bool):
    from services.fujian_check import cache
    from services.fujian_check.pdf_reader import file_sha256, parse_pdf

    p = Path(path)
    if p.suffix.lower() == ".docx":
        from services.fujian_check.docx_reader import parse_docx

        return parse_docx(str(p))
    if not no_cache:
        cached = cache.load_parsed(file_sha256(p))
        if cached is not None:
            return cached
    doc = parse_pdf(str(p))
    if not no_cache:
        cache.save_parsed(doc)
    return doc


def _tender_summary(req) -> dict:
    return {
        "profile": req.profile.model_dump(),
        "project_name": req.project_name,
        "project_code": req.project_code,
        "sections": [s.model_dump() for s in req.sections],
        "qianfubiao_rows": len(req.qianfubiao),
        "rejection_counts": {g: sum(1 for c in req.rejection if c.group == g)
                             for g in ("资格文件", "商务初审", "详细评审", "技术文件", "须知正文")},
        "datasheet_rows": len(req.datasheet),
        "forms": {s: len(req.forms_in(s)) for s in ("资格文件", "商务文件", "技术文件", "定标文件")},
        "hard": req.hard.model_dump(exclude={"sources"}),
        "staffing": req.staffing.model_dump(exclude={"source"}) if req.staffing else None,
        "dangerous_works": [d.value for d in req.dangerous_works],
        "appendix": {k: v.value for k, v in req.appendix_params.items()},
        "parse_warnings": req.parse_warnings,
    }


def cmd_parse_tender(args) -> int:
    from services.fujian_check.profiles import detect_profile
    from services.fujian_check.tender.extract import extract_requirements

    t0 = time.time()
    doc = _load(args.file, args.no_cache)
    req = extract_requirements(doc, detect_profile(doc))
    summary = _tender_summary(req)
    summary["elapsed_s"] = round(time.time() - t0, 1)
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "requirements": req.model_dump()},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_parse_bid(args) -> int:
    from services.fujian_check.bid.index import build_bid_index
    from services.fujian_check.profiles import detect_profile, get_profile
    from services.fujian_check.tender.extract import extract_requirements

    t0 = time.time()
    tdoc = _load(args.tender, args.no_cache)
    prof = detect_profile(tdoc)
    req = extract_requirements(tdoc, prof)
    bdoc = _load(args.file, args.no_cache)
    idx = build_bid_index(bdoc, req, get_profile(prof.key))
    summary = {
        "bidder": idx.bidder_name,
        "sections": idx.sections,
        "forms": [{"section": f.form.section if f.form else None, "no": f.form.no if f.form else None,
                   "title": f.form.title if f.form else f.matched_title, "match": f.match_kind,
                   "pages": [f.page_start, f.page_end], "scanned": f.scanned_pages} for f in idx.forms],
        "letter": idx.letter.model_dump(exclude={"loc"}) if idx.letter else None,
        "boq_cover": idx.boq_cover,
        "boq_summary": {k: v.value for k, v in idx.boq_summary.items()},
        "personnel": [p.model_dump() for p in idx.personnel.people] if idx.personnel else [],
        "tech_segments": [t.model_dump() for t in idx.tech_segments],
        "scanned_pages": len(idx.scanned_pages),
        "parse_warnings": idx.parse_warnings,
        "elapsed_s": round(time.time() - t0, 1),
    }
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "index": idx.model_dump()},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_check(args) -> int:
    from services.fujian_check.engine import run_check
    from services.fujian_check.report import to_markdown, to_platform_payload

    llm = None
    if not args.no_llm:
        try:
            from services.llm_factory import get_llm_gateway   # 挂在 BidMaster-Pro 平台里时用平台网关

            llm = get_llm_gateway()
        except Exception:
            from services.fujian_check.lite.server import SimpleLLM, _llm_conf, _apply_settings, _load_env

            _load_env()
            _apply_settings()
            conf = _llm_conf()
            if conf:
                llm = SimpleLLM(conf)
            else:
                print("[warn] 未配置 LLM key（lite_data/settings.json 或环境变量 FJ_LLM_API_KEY），按 --no-llm 运行", file=sys.stderr)
    rule_ids = [r.strip() for r in args.rules.split(",")] if args.rules else None

    async def _progress(p: float, msg: str):
        print(f"[{p * 100:5.1f}%] {msg}", file=sys.stderr)

    report = asyncio.run(run_check(args.tender, args.bid, llm=llm, use_llm=not args.no_llm,
                                   use_ocr=not args.no_ocr, use_cache=not args.no_cache,
                                   rule_ids=rule_ids, progress=_progress))
    payload = to_platform_payload(report)
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(to_markdown(report), encoding="utf-8")
    print(json.dumps({"risk_level": report.risk_level, "has_critical_issues": report.has_critical_issues,
                      "summary": report.summary, "stats": report.stats.model_dump()}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fujian_check")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("parse-tender")
    p1.add_argument("--file", required=True)
    p1.add_argument("--out")
    p1.add_argument("--no-cache", action="store_true")
    p1.set_defaults(fn=cmd_parse_tender)

    p2 = sub.add_parser("parse-bid")
    p2.add_argument("--tender", required=True)
    p2.add_argument("--file", required=True)
    p2.add_argument("--out")
    p2.add_argument("--no-cache", action="store_true")
    p2.set_defaults(fn=cmd_parse_bid)

    p3 = sub.add_parser("check")
    p3.add_argument("--tender", required=True)
    p3.add_argument("--bid", required=True)
    p3.add_argument("--out")
    p3.add_argument("--md")
    p3.add_argument("--no-llm", action="store_true")
    p3.add_argument("--no-ocr", action="store_true")
    p3.add_argument("--no-cache", action="store_true")
    p3.add_argument("--rules")
    p3.set_defaults(fn=cmd_check)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
