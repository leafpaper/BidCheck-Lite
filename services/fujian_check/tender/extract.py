"""招标文件 → TenderRequirements 编排。"""
from __future__ import annotations

from services.fujian_check.models import ParsedDoc, TenderProfile, TenderRequirements
from services.fujian_check.profiles import get_profile
from services.fujian_check.tender.appendix import find_appendix_pages, parse_appendix
from services.fujian_check.tender.datasheet import (
    find_dangerous_works,
    find_similar_projects,
    find_staffing,
    parse_datasheet,
)
from services.fujian_check.tender.forms_catalog import parse_required_forms
from services.fujian_check.tender.qianfubiao import extract_hard_values, parse_qianfubiao, project_identity
from services.fujian_check.tender.rejection import parse_rejection_clauses
from services.fujian_check.tender.sections import find_section, locate_sections


def extract_requirements(doc: ParsedDoc, profile: TenderProfile) -> TenderRequirements:
    if profile.key == "fujian_gov_cs":
        from services.fujian_check.tender.gov_cs import extract_gov_cs

        return extract_gov_cs(doc, profile)
    prof = get_profile(profile.key)
    req = TenderRequirements(profile=profile)
    req.sections = locate_sections(doc, prof)
    warn = req.parse_warnings

    ch2s1 = find_section(req.sections, 2, 1)
    ch2 = find_section(req.sections, 2, None)
    ch3 = find_section(req.sections, 3, None)
    ch8 = find_section(req.sections, 8, None)

    if ch2s1 is None and ch2 is not None:
        # 没有节标题时，前附表按第2章前 25 页兜底
        from services.fujian_check.models import Section

        ch2s1 = Section(chapter=2, section=1, title="投标须知前附表", page_start=ch2.page_start,
                        page_end=min(ch2.page_end, ch2.page_start + 24))
        warn.append("未找到第2章第1节标题，前附表按第2章起始 25 页兜底")

    if ch2s1:
        req.qianfubiao = parse_qianfubiao(doc, ch2s1)
        if len(req.qianfubiao) < 20:
            warn.append(f"前附表仅解析出 {len(req.qianfubiao)} 行，低于预期")
        nums = [r.item_no for r in req.qianfubiao]
        missing = [n for n in range(1, max(nums, default=0) + 1) if n not in nums]
        if missing:
            warn.append(f"前附表缺项号: {missing}")
        req.hard = extract_hard_values(req.qianfubiao, doc)
        req.project_name, req.project_code = project_identity(req.qianfubiao)
    else:
        warn.append("未找到第2章，无法解析前附表")

    if ch3:
        req.datasheet = parse_datasheet(doc, ch3)
        req.staffing = find_staffing(req.datasheet)
        req.dangerous_works = find_dangerous_works(req.datasheet)
        sp = find_similar_projects(req.datasheet)
        if sp is not None and req.hard.similar_projects_required is None:
            req.hard.similar_projects_required = sp
        if not req.datasheet:
            warn.append("未解析出评标办法和标准数据表")
    else:
        warn.append("未找到第3章")

    req.rejection = parse_rejection_clauses(doc, req.sections, prof)
    if not any(c.group == "资格文件" for c in req.rejection):
        warn.append("未抽到资格文件否决条款(3.1.x)")

    if ch8:
        req.forms = parse_required_forms(doc, ch8, prof)
        pages = find_appendix_pages(doc, range(ch8.page_start, ch8.page_end + 1))
        req.appendix_params = parse_appendix(doc, pages, "tender", "第8章 投标文件格式")
        if not req.forms:
            warn.append("第8章未解析出表单清单")
    else:
        warn.append("未找到第8章")

    return req
