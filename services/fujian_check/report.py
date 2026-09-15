"""报告组装：汇总/风险等级 + 平台 payload（满足导出 skill 形状）+ Markdown。"""
from __future__ import annotations

from collections import Counter, defaultdict

from services.fujian_check import ENGINE_VERSION
from services.fujian_check.models import BidIndex, CheckReport, Finding, Location, RunStats, TenderRequirements

GROUP_ORDER = ["资格文件否决", "商务初审否决", "详细评审否决", "技术文件暗标", "前附表硬值", "交叉一致性",
               "表单齐全性", "人员配备", "电子标", "扫描件证书"]


def _risk(findings: list[Finding]) -> tuple[str, bool]:
    critical = any(f.severity == "reject" and f.verdict == "fail" for f in findings)
    if critical:
        return "high", True
    fails = sum(1 for f in findings if f.verdict == "fail")
    warns = sum(1 for f in findings if f.verdict == "warning")
    if fails or warns >= 3:
        return "medium", False
    return "low", False


def build_report(findings: list[Finding], req: TenderRequirements, idx: BidIndex, meta: dict,
                 stats: RunStats | None = None) -> CheckReport:
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    findings = sorted(findings, key=lambda f: (order.get(f.group, 99), f.rule_id))
    c = Counter(f.verdict for f in findings)
    by_group: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for f in findings:
        by_group[f.group][f.verdict] += 1
    risk, critical = _risk(findings)
    return CheckReport(
        version=ENGINE_VERSION,
        profile=req.profile,
        tender=meta.get("tender", {}),
        bid=meta.get("bid", {}),
        hard=req.hard,
        findings=findings,
        summary={"total": len(findings), "passed": c["pass"], "failed": c["fail"], "warning": c["warning"],
                 "manual": c["manual"], "na": c["na"], "by_group": {g: dict(v) for g, v in by_group.items()}},
        risk_level=risk,  # type: ignore[arg-type]
        has_critical_issues=critical,
        stats=stats or RunStats(),
    )


def _fmt_loc(loc: Location | None) -> str:
    return loc.label() if loc else "（未定位）"


_STATUS_MAP = {"pass": "pass", "fail": "fail", "warning": "warning", "manual": "warning", "na": "pass"}


def to_platform_payload(report: CheckReport) -> dict:
    checks = []
    for f in report.findings:
        ev = "；".join(_fmt_loc(e) for e in f.evidence[:3]) if f.evidence else "（未找到）"
        checks.append({
            "check_name": f"[{f.rule_id}] {f.title}",
            "required": f.requirement[:200],
            "actual": f.actual[:200],
            "status": _STATUS_MAP[f.verdict],
            "detail": f"{f.missing or f.verdict} | 招标: {_fmt_loc(f.requirement_loc)} | 投标: {ev}",
            "suggestion": f.fix,
        })
    s = report.summary
    return {
        "engine": report.engine,
        "version": report.version,
        "profile": report.profile.model_dump(),
        "tender": report.tender,
        "bid": report.bid,
        "checks": checks,
        "summary": {"total": s.get("total", 0), "passed": s.get("passed", 0), "failed": s.get("failed", 0),
                    "warning": s.get("warning", 0) + s.get("manual", 0), "manual": s.get("manual", 0),
                    "na": s.get("na", 0), "by_group": s.get("by_group", {})},
        "risk_level": report.risk_level,
        "has_critical_issues": report.has_critical_issues,
        "findings": [f.model_dump() for f in report.findings],
        "hard_values": report.hard.model_dump(exclude={"sources"}),
        "stats": report.stats.model_dump(),
    }


_TITLES = {"fujian_platform": "福建施工标投标文件检查报告", "fujian_gov_cs": "政府采购磋商响应文件检查报告", "jiubuwei": "九部委版投标文件检查报告"}
_ICON = {"pass": "✅", "fail": "❌", "warning": "⚠️", "manual": "👁", "na": "—"}


def to_markdown(report: CheckReport) -> str:
    s = report.summary
    lines = [
        f"# {_TITLES.get(report.profile.key, '投标文件检查报告')}",
        "",
        f"- 招标文件：{report.tender.get('name', '')}（{report.tender.get('pages', '?')} 页）",
        f"- 投标文件：{report.bid.get('name', '')}（{report.bid.get('pages', '?')} 页）",
        f"- 模板：{report.profile.key}（置信度 {report.profile.confidence}）",
        f"- **风险等级：{report.risk_level}**  否决级问题：{'有' if report.has_critical_issues else '无'}",
        f"- 通过 {s.get('passed', 0)} / 不通过 {s.get('failed', 0)} / 警告 {s.get('warning', 0)} / 待人工 {s.get('manual', 0)} / 不适用 {s.get('na', 0)}",
        f"- LLM 调用 {report.stats.llm_calls} 次，OCR {report.stats.ocr_pages} 页，耗时 {report.stats.elapsed_s}s",
        "",
        "## 招标硬值",
        "",
    ]
    h = report.hard
    for k, label in (("control_price", "招标控制价"), ("duration_days", "工期(日历天)"), ("validity_days", "投标有效期(天)"),
                     ("deposit", "投标保证金"), ("deadline", "投标截止")):
        v = getattr(h, k)
        if v is None:
            continue
        v = v.value if hasattr(v, "value") else v
        lines.append(f"- {label}：{v}")
    lines.append("")
    cur = None
    for f in report.findings:
        if f.group != cur:
            cur = f.group
            lines += ["", f"## {cur}", ""]
        lines.append(f"### {_ICON[f.verdict]} [{f.rule_id}] {f.title}  `{f.verdict}`")
        lines.append(f"- 招标要求：{f.requirement}  （{_fmt_loc(f.requirement_loc)}）")
        lines.append(f"- 投标实际：{f.actual or '—'}")
        if f.evidence:
            lines.append("- 证据位置：" + "；".join(_fmt_loc(e) for e in f.evidence[:4]))
        if f.missing:
            lines.append(f"- 缺什么：{f.missing}")
        if f.fix:
            lines.append(f"- 怎么改：{f.fix}")
        lines.append("")
    return "\n".join(lines)
