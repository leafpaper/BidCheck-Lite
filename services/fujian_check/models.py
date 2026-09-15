"""fujian_check 全部数据模型（pydantic v2）。

约定：
- 所有"位置"统一用 Location，招标文件与投标文件共用；PDF 有页码，docx 用段落号 + 标题路径。
- 引擎内部只传这些模型；对平台（CheckReport.results / 导出 skill / 前端）的形状由 report.to_platform_payload 负责。
"""
from __future__ import annotations

import re
from typing import Iterable, Literal

from pydantic import BaseModel, Field

# ──────────────────────────────────────────────
# 解析层
# ──────────────────────────────────────────────

BlockKind = Literal["text", "table_cell", "heading"]


class Block(BaseModel):
    page: int | None = None          # PDF 1-based；docx 为 None
    order: int                       # 全文顺序号（0 起）
    bbox: tuple[float, float, float, float] | None = None
    text: str                        # 已归一化（textnorm）
    kind: BlockKind = "text"
    para_index: int | None = None    # docx 段落序号
    heading_path: list[str] = Field(default_factory=list)


class Hit(BaseModel):
    page: int | None
    block_order: int
    match: str
    line: str


class Page(BaseModel):
    page: int
    char_count: int
    is_scanned: bool = False         # 文字层 < 30 字
    is_toc: bool = False             # 目录页（同页 ≥5 个章标题或大量"……(数字)"）
    footer_stripped: str | None = None
    blocks: list[Block] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


class Table(BaseModel):
    page: int
    index: int
    rows: list[list[str]]


class ParsedDoc(BaseModel):
    path: str
    file_hash: str
    kind: Literal["pdf", "docx"]
    parser_version: str
    n_pages: int
    pages: list[Page]
    tables: list[Table] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)

    # ---- 便捷访问 ----
    def page_text(self, p: int) -> str:
        if 1 <= p <= len(self.pages):
            return self.pages[p - 1].text
        return ""

    def text_range(self, p0: int, p1: int) -> str:
        p0 = max(1, p0)
        p1 = min(self.n_pages, p1)
        return "\n".join(f"[p{p}]\n{self.page_text(p)}" for p in range(p0, p1 + 1))

    def iter_lines(self, pages: Iterable[int] | None = None):
        """逐行产出 (page, block_order, line)。"""
        idx = range(1, self.n_pages + 1) if pages is None else pages
        for p in idx:
            if not (1 <= p <= len(self.pages)):
                continue
            for b in self.pages[p - 1].blocks:
                for line in b.text.split("\n"):
                    if line.strip():
                        yield p, b.order, line

    def find(self, pattern: str, pages: Iterable[int] | None = None, flags: int = 0) -> list[Hit]:
        rx = re.compile(pattern, flags)
        hits: list[Hit] = []
        for p, order, line in self.iter_lines(pages):
            m = rx.search(line)
            if m:
                hits.append(Hit(page=p, block_order=order, match=m.group(0), line=line))
        return hits

    def tables_on(self, page: int) -> list[Table]:
        return [t for t in self.tables if t.page == page]


# ──────────────────────────────────────────────
# 位置 / profile
# ──────────────────────────────────────────────


class Location(BaseModel):
    doc: Literal["tender", "bid"]
    page: int | None = None
    page_end: int | None = None
    section: str | None = None       # "第2章第1节 投标须知前附表" / "第2节 商务文件"
    form: str | None = None          # "一、投标函"
    clause: str | None = None        # "3.1.8" / "前附表第17.1项"
    para_index: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    excerpt: str = ""                # ≤300 字原文

    def label(self) -> str:
        parts: list[str] = []
        if self.section:
            parts.append(self.section)
        if self.form:
            parts.append(self.form)
        if self.clause:
            parts.append(self.clause)
        if self.page is not None:
            tail = f"-{self.page_end}" if self.page_end and self.page_end != self.page else ""
            parts.append(f"p{self.page}{tail}")
        elif self.para_index is not None:
            parts.append(f"段落#{self.para_index}")
            if self.heading_path:
                parts.append("/".join(self.heading_path))
        return " · ".join(parts) if parts else "（未定位）"


ProfileKey = Literal["fujian_platform", "fujian_gov_cs", "jiubuwei", "unknown"]


class TenderProfile(BaseModel):
    key: ProfileKey
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    variant: dict[str, bool] = Field(default_factory=dict)   # {"评定分离":True,"暗标":True,"联合体":True}


class Section(BaseModel):
    chapter: int
    section: int | None = None
    title: str
    page_start: int
    page_end: int


# ──────────────────────────────────────────────
# 招标文件结构化结果
# ──────────────────────────────────────────────


class QfbRow(BaseModel):
    """投标须知前附表一行。"""
    item_no: int
    clause: str                      # "1.7/21.2"
    name: str
    content: str
    loc: Location


RejectionGroup = Literal["资格文件", "商务初审", "详细评审", "技术文件", "须知正文", "其他"]


class RejectionClause(BaseModel):
    id: str                          # "3.1.8" / "5.1.6" / "7.1.1" / "4.3" / "须知25.2.6"
    group: RejectionGroup
    text: str
    loc: Location
    refs: list[str] = Field(default_factory=list)


class DataSheetRow(BaseModel):
    no: int
    clause: str
    name: str
    content: str
    loc: Location


class StaffRole(BaseModel):
    count: int
    cert: str | None = None          # "贰级建造师" / "C证" / "中级职称"
    extra: list[str] = Field(default_factory=list)


class StaffingRequirement(BaseModel):
    roles: dict[str, StaffRole] = Field(default_factory=dict)
    one_person_one_post: bool = True
    source: Location | None = None


FormSection = Literal["资格文件", "商务文件", "技术文件", "定标文件", "响应文件"]


class RequiredForm(BaseModel):
    section: FormSection
    no: str                          # "十"
    title: str                       # "拟派出施工现场管理人员表"
    optional: bool = False           # 标题含"（如有时）"
    aliases: list[str] = Field(default_factory=list)
    loc: Location | None = None


class Money(BaseModel):
    value: int                       # 元，整数（分位四舍五入）
    raw: str = ""

    def __int__(self) -> int:
        return self.value


class JVRule(BaseModel):
    allowed: bool
    max_members: int | None = None
    raw: str = ""


class Sourced(BaseModel):
    """带来源位置的标量值。"""
    value: str | int | float | bool | None = None
    loc: Location | None = None


class HardValues(BaseModel):
    control_price: Money | None = None
    duration_days: int | None = None
    quota_duration_days: int | None = None
    quality: str | None = None
    validity_days: int | None = None
    deposit: Money | None = None
    deposit_forms: list[str] = Field(default_factory=list)
    qualification: str | None = None
    pm_requirement: str | None = None
    tech_lead_requirement: str | None = None
    joint_venture: JVRule | None = None
    similar_projects_required: int | None = None
    credit_score_applied: bool | None = None
    subcontract_policy: str | None = None
    ca_own_only: bool | None = None
    xml_required: bool | None = None
    deadline: str | None = None
    performance_bond: str | None = None
    notice_date: str | None = None               # 招标文件/公告日期 YYYY-MM-DD（签署日期下限）
    similar_projects_years: int | None = None    # 类似工程业绩「前 N 年内」
    social_start_offset: int | None = None       # 社保：截止日前「上 N 个月」为始点
    social_months: int | None = None             # 社保：连续缴费累计 M 个月
    sources: dict[str, Location] = Field(default_factory=dict)   # 字段名 → 来源


class TenderRequirements(BaseModel):
    profile: TenderProfile
    sections: list[Section] = Field(default_factory=list)
    qianfubiao: list[QfbRow] = Field(default_factory=list)
    rejection: list[RejectionClause] = Field(default_factory=list)
    datasheet: list[DataSheetRow] = Field(default_factory=list)
    staffing: StaffingRequirement | None = None
    forms: list[RequiredForm] = Field(default_factory=list)
    appendix_params: dict[str, Sourced] = Field(default_factory=dict)   # 投标函附录 7 行
    hard: HardValues = Field(default_factory=HardValues)
    dangerous_works: list[Sourced] = Field(default_factory=list)       # 数据表 4.2 危大工程清单标题
    project_name: str = ""
    project_code: str = ""
    parse_warnings: list[str] = Field(default_factory=list)

    def qfb(self, clause_prefix: str) -> QfbRow | None:
        for r in self.qianfubiao:
            parts = [x.strip() for x in r.clause.split("/")]
            if clause_prefix in parts:
                return r
        return None

    def forms_in(self, section: FormSection) -> list[RequiredForm]:
        return [f for f in self.forms if f.section == section]


# ──────────────────────────────────────────────
# 投标文件索引
# ──────────────────────────────────────────────

MatchKind = Literal["exact", "alias", "fuzzy", "scanned", "missing"]
# scanned = 文字层找不到标题，但相邻区间存在扫描页，疑似以扫描件形式提交（需 OCR 确认）


class FormRange(BaseModel):
    form: RequiredForm | None = None
    matched_title: str = ""
    page_start: int | None = None
    page_end: int | None = None
    scanned_pages: list[int] = Field(default_factory=list)
    match_kind: MatchKind = "missing"
    para_start: int | None = None
    para_end: int | None = None


class BidLetter(BaseModel):
    total_price: Money | None = None
    total_price_cn: str | None = None
    total_price_cn_value: Money | None = None
    duration_days: int | None = None
    quota_duration_days: int | None = None
    quality: str | None = None
    validity_days: int | None = None
    deposit: Money | None = None
    deposit_cn: str | None = None
    pm_name: str | None = None
    pm_cert_no: str | None = None
    bidder: str = ""
    signer: str | None = None
    date: str | None = None
    project_name: str | None = None
    loc: Location | None = None


class Person(BaseModel):
    name: str
    post: str                        # "项目负责人" / "施工员"
    id_no: str | None = None
    cert: str | None = None
    cert_no: str | None = None
    title: str | None = None         # 职称
    reg_major: str | None = None     # 注册专业（建造师）
    cert_valid_end: str | None = None  # 注册证书使用有效期止 YYYY-MM-DD
    page: int | None = None
    para_index: int | None = None


class PersonnelTable(BaseModel):
    people: list[Person] = Field(default_factory=list)
    loc: Location | None = None
    query_valid_end: str | None = None   # 平台打印件「查询有效期」止日
    project_code: str | None = None      # 表头「招标项目编号」

    def by_post(self, post: str) -> list[Person]:
        return [p for p in self.people if post in p.post]


class TechSegment(BaseModel):
    title: str
    page_start: int | None = None
    page_end: int | None = None
    para_start: int | None = None
    para_end: int | None = None


class BidIndex(BaseModel):
    sections: dict[str, tuple[int, int]] = Field(default_factory=dict)   # {"资格文件":(2,49),...}
    forms: list[FormRange] = Field(default_factory=list)
    scanned_pages: list[int] = Field(default_factory=list)
    scanned_attribution: dict[int, str] = Field(default_factory=dict)      # page → form title
    letter: BidLetter | None = None
    appendix: dict[str, Sourced] = Field(default_factory=dict)
    boq_cover: dict[str, str] = Field(default_factory=dict)                # 封3：total, total_cn
    boq_summary: dict[str, Money] = Field(default_factory=dict)            # 表2：合计/安全文明施工费/暂列金额
    boq_page_range: tuple[int, int] | None = None
    personnel: PersonnelTable | None = None
    tech_segments: list[TechSegment] = Field(default_factory=list)
    bidder_name: str = ""
    legal_rep: str | None = None
    legal_rep_id: str | None = None                                        # 法定代表人身份证号（资格证明书文字层）
    form_dates: dict[str, Sourced] = Field(default_factory=dict)           # 表单标题 → 落款日期 YYYY-MM-DD
    basic_account: str | None = None                                       # 投标人基本账户账号
    docx_mode: bool = False
    parse_warnings: list[str] = Field(default_factory=list)

    def form(self, title_part: str) -> FormRange | None:
        for fr in self.forms:
            if fr.form and title_part in fr.form.title:
                return fr
            if title_part in fr.matched_title:
                return fr
        return None


# ──────────────────────────────────────────────
# 规则 / 结论 / 报告
# ──────────────────────────────────────────────

Verdict = Literal["pass", "fail", "warning", "manual", "na"]
Severity = Literal["reject", "major", "minor", "info"]
Method = Literal["rule", "table", "llm", "ocr", "manual"]


class EvidenceWindow(BaseModel):
    loc: Location
    text: str
    score: float = 0.0


class JudgeResult(BaseModel):
    verdict: Literal["pass", "fail", "warning", "manual"]
    quote: str = ""
    reason: str = ""
    confidence: float = 0.0


class Finding(BaseModel):
    rule_id: str
    group: str
    title: str
    severity: Severity
    verdict: Verdict
    requirement: str = ""
    requirement_loc: Location | None = None
    actual: str = ""
    evidence: list[Location] = Field(default_factory=list)
    missing: str = ""
    fix: str = ""
    method: Method = "rule"
    confidence: float = 1.0
    supersedes: list[str] = Field(default_factory=list)


class RunStats(BaseModel):
    llm_calls: int = 0
    ocr_pages: int = 0
    cache_hits: int = 0
    elapsed_s: float = 0.0
    parse_warnings: list[str] = Field(default_factory=list)
    degraded_rules: list[str] = Field(default_factory=list)   # 超预算降级为 manual 的规则


class CheckReport(BaseModel):
    engine: str = "fujian_check"
    version: str
    profile: TenderProfile
    tender: dict = Field(default_factory=dict)      # {name, hash, pages, kind}
    bid: dict = Field(default_factory=dict)
    hard: HardValues = Field(default_factory=HardValues)
    findings: list[Finding] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)     # {total,passed,failed,warning,manual,na,by_group}
    risk_level: Literal["low", "medium", "high"] = "low"
    has_critical_issues: bool = False
    stats: RunStats = Field(default_factory=RunStats)
