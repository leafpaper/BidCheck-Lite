"""规则协议与上下文。

每条规则：id / group / title / severity / method / supersedes（替代的旧 check key）+ async run(ctx) -> list[Finding]。
规则只读 ctx，不改状态；LLM/OCR 通过 ctx 的服务对象调用并自动计数。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from services.fujian_check.models import (
    BidIndex,
    Finding,
    FormRange,
    Location,
    Method,
    ParsedDoc,
    RunStats,
    Severity,
    TenderRequirements,
    Verdict,
)


@dataclass
class RuleContext:
    req: TenderRequirements
    idx: BidIndex
    tdoc: ParsedDoc
    bdoc: ParsedDoc
    llm: Any = None
    use_llm: bool = True
    use_ocr: bool = True
    stats: RunStats = field(default_factory=RunStats)
    ocr_text: dict[int, str] = field(default_factory=dict)      # 投标 PDF 页 → OCR 文本（engine 预填）
    ocr_budget: int = 40
    llm_budget: int = 25
    log: Callable[[str], None] | None = None

    def note(self, msg: str) -> None:
        if self.log:
            self.log(msg)

    # ---- 便捷定位 ----
    def form(self, title_part: str) -> FormRange | None:
        return self.idx.form(title_part)

    def form_loc(self, fr: FormRange | None, excerpt: str = "") -> Location | None:
        if fr is None or fr.page_start is None:
            return None
        title = fr.form.title if fr.form else fr.matched_title
        sec = fr.form.section if fr.form else None
        return Location(doc="bid", page=fr.page_start, page_end=fr.page_end, section=sec, form=title,
                        excerpt=excerpt[:300])

    def bid_page_loc(self, page: int, form: str | None = None, excerpt: str = "") -> Location:
        sec = next((name for name, (s, e) in self.idx.sections.items() if s <= page <= e), None)
        return Location(doc="bid", page=page, section=sec, form=form, excerpt=excerpt[:300])

    def clause_loc(self, clause_id: str) -> Location | None:
        for c in self.req.rejection:
            if c.id == clause_id:
                return c.loc
        return None

    def qfb_loc(self, clause: str) -> Location | None:
        r = self.req.qfb(clause)
        return r.loc if r else None


class Rule:
    id: str = ""
    group: str = ""
    title: str = ""
    severity: Severity = "major"
    method: Method = "rule"
    supersedes: list[str] = []
    source_clause: str = ""          # 触发本规则的招标条款号（用于 requirement_loc 兜底）

    async def run(self, ctx: RuleContext) -> list[Finding]:  # pragma: no cover - 抽象
        raise NotImplementedError

    def ocr_pages_needed(self, ctx: RuleContext) -> list[int]:
        """method == 'ocr' 的规则声明需要视觉识别的投标 PDF 页；engine 批量预取后写入 ctx.ocr_text。"""
        return []

    # ---- Finding 构造 ----
    def make(self, verdict: Verdict, *, requirement: str = "", requirement_loc: Location | None = None,
             actual: str = "", evidence: list[Location] | None = None, missing: str = "", fix: str = "",
             method: Method | None = None, confidence: float = 1.0) -> Finding:
        return Finding(rule_id=self.id, group=self.group, title=self.title, severity=self.severity, verdict=verdict,
                       requirement=requirement[:600], requirement_loc=requirement_loc, actual=actual[:600],
                       evidence=[e for e in (evidence or []) if e is not None], missing=missing[:400], fix=fix[:400],
                       method=method or self.method, confidence=confidence, supersedes=list(self.supersedes))

    def na(self, reason: str, requirement_loc: Location | None = None) -> Finding:
        return self.make("na", requirement=reason, requirement_loc=requirement_loc, actual="不适用")

    def manual(self, requirement: str, actual: str, evidence: list[Location] | None = None,
               requirement_loc: Location | None = None, fix: str = "") -> Finding:
        return self.make("manual", requirement=requirement, requirement_loc=requirement_loc, actual=actual,
                         evidence=evidence, missing="需人工核对", fix=fix or "请按证据位置人工核对", method="manual",
                         confidence=0.5)


RuleFn = Callable[[RuleContext], Awaitable[list[Finding]]]
