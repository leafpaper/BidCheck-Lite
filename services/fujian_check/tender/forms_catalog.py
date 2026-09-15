"""第8章 投标文件格式：各节要求提交的表单清单。

策略：在第8章范围内按"第N 节 资格文件/商务文件/技术文件"与"附件2 定标文件"切段；
每段内收集 `一、标题` 行（目录页 + 正文标题），按编号去重、按编号排序；
目录页的"（如有时）"标记 optional；正文里新增的编号（如本项目修订追加的十六～二十一）也纳入。
"""
from __future__ import annotations

import re

from services.fujian_check.models import FormSection, Location, ParsedDoc, RequiredForm, Section
from services.fujian_check.profiles.base import Profile
from services.fujian_check.textnorm import cn_to_int, to_halfwidth

_FORM_RE = re.compile(r"^([一二三四五六七八九十]{1,3})、\s*(\S.*?)\s*[.…·]*\s*(?:\(\s*\)|（\s*）)?$")
_SEC_RE = re.compile(r"^第\s*(\d)\s*节\s*(资格文件|商务文件|技术文件)")
_AWARD_RE = re.compile(r"^附件\s*2\s*定标文件|^定标文件\s*[(（]格式[)）]")
_OPTIONAL_RE = re.compile(r"[（(]\s*(如有时|如有|若有)\s*[)）]")
_STRIP_RE = re.compile(r"[…\.·]{2,}.*$")
_MAX_FORM_NO = 40


def parse_required_forms(doc: ParsedDoc, ch8: Section, profile: Profile | None = None) -> list[RequiredForm]:
    cur: FormSection | None = None
    found: dict[tuple[str, str], RequiredForm] = {}
    for p, _o, line in doc.iter_lines(range(ch8.page_start, ch8.page_end + 1)):
        ln = to_halfwidth(line).strip()
        ms = _SEC_RE.match(ln)
        if ms:
            cur = ms.group(2)  # type: ignore[assignment]
            continue
        if _AWARD_RE.match(ln):
            cur = "定标文件"
            continue
        if cur is None or cur == "技术文件":
            # 技术文件为暗标，无表单清单；其后的定标方案标题不是表单
            continue
        m = _FORM_RE.match(ln)
        if not m:
            continue
        no_cn, title = m.group(1), m.group(2)
        n = cn_to_int(no_cn)
        if n is None or n > _MAX_FORM_NO:
            continue
        optional = bool(_OPTIONAL_RE.search(title))
        title_clean = _STRIP_RE.sub("", _OPTIONAL_RE.sub("", title)).strip(" :：")
        if len(title_clean) < 2 or len(title_clean) > 40:
            continue
        # 排除承诺函正文里的"一、二、"编号句（含标点或过长）
        if re.search(r"[,，。;；]", title_clean):
            continue
        key = (cur, no_cn)
        if key in found:
            if optional:
                found[key].optional = True
            continue
        aliases = []
        if profile:
            for canon, al in profile.form_aliases.items():
                if canon in title_clean or title_clean in canon:
                    aliases = al
                    break
        found[key] = RequiredForm(section=cur, no=no_cn, title=title_clean, optional=optional, aliases=aliases,
                                  loc=Location(doc="tender", page=p, section="第8章 投标文件格式",
                                               form=f"{no_cn}、{title_clean}"))
    forms = list(found.values())
    order = {"资格文件": 0, "商务文件": 1, "技术文件": 2, "定标文件": 3}
    forms.sort(key=lambda f: (order[f.section], cn_to_int(f.no) or 0))
    return forms


def award_section(doc: ParsedDoc, ch8: Section) -> Section | None:
    for p in range(ch8.page_start, ch8.page_end + 1):
        for ln in doc.page_text(p).split("\n")[:3]:
            if _AWARD_RE.match(to_halfwidth(ln).strip()):
                return Section(chapter=8, section=99, title="定标文件(格式)", page_start=p, page_end=ch8.page_end)
    return None
