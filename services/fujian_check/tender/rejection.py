"""否决条款抽取。

福建平台版：第3章正文 3.1.x（资格文件）、5.1.x（商务初审）、7.1.x（详细评审）、4.x（技术文件）、6.2.2；
第2章正文里含"否决"的句子归"须知正文"。
"""
from __future__ import annotations

import re

from services.fujian_check.models import Location, ParsedDoc, RejectionClause, Section
from services.fujian_check.profiles.base import Profile

_CLAUSE_RE = re.compile(r"^(3\.1|5\.1|7\.1)\.(\d{1,2})\s*(\S.*)$")
_TECH_RE = re.compile(r"^4\.(\d)\s+(\S.*)$")
_ANY_NUM_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,3}\.?\s")
_HEADING_RE = re.compile(r"^\d{1,2}\.\s*\S{2,}(评审|办法|标准|修正|推荐|提交)")
_REF_RE = re.compile(r"数据表第\s*(\d+)\s*项|投标须知(?:前附表)?第?\s*([\d\.]+)\s*[款项]?")
_NOTICE_REJECT_RE = re.compile(r"否决(?:其|全部)?投标|作否决投标处理|按否决投标处理")
_NOTICE_NUM_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,2})")


def _refs(text: str) -> list[str]:
    out = []
    for m in _REF_RE.finditer(text):
        if m.group(1):
            out.append(f"数据表第{m.group(1)}项")
        elif m.group(2):
            out.append(f"须知{m.group(2)}")
    return out


def parse_rejection_clauses(doc: ParsedDoc, sections: list[Section], profile: Profile) -> list[RejectionClause]:
    ch3 = next((s for s in sections if s.chapter == 3 and s.section is None), None)
    ch2 = next((s for s in sections if s.chapter == 2 and s.section is None), None)
    out: list[RejectionClause] = []

    if ch3:
        cur: RejectionClause | None = None
        for p, _o, line in doc.iter_lines(range(ch3.page_start, ch3.page_end + 1)):
            if doc.pages[p - 1].is_toc:
                continue
            m = _CLAUSE_RE.match(line)
            if m:
                prefix = m.group(1)
                cid = f"{prefix}.{m.group(2)}"
                if any(c.id == cid for c in out):
                    cur = None
                    continue
                group = profile.rejection_groups.get(prefix, "其他")
                cur = RejectionClause(id=cid, group=group, text=m.group(3).strip(),  # type: ignore[arg-type]
                                      loc=Location(doc="tender", page=p, section="第3章 评标办法和标准", clause=cid))
                out.append(cur)
                continue
            mt = _TECH_RE.match(line)
            if mt and "否决" in line:
                cid = f"4.{mt.group(1)}"
                if not any(c.id == cid for c in out):
                    cur = RejectionClause(id=cid, group="技术文件", text=mt.group(2).strip(),
                                          loc=Location(doc="tender", page=p, section="第3章 评标办法和标准", clause=cid))
                    out.append(cur)
                else:
                    cur = None
                continue
            if line.startswith("6.2.2") and not any(c.id == "6.2.2" for c in out):
                cur = RejectionClause(id="6.2.2", group="商务初审", text=line[5:].strip(),
                                      loc=Location(doc="tender", page=p, section="第3章 评标办法和标准", clause="6.2.2"))
                out.append(cur)
                continue
            # 续行：直到下一个编号行 / 标题行
            if cur is not None:
                if _ANY_NUM_RE.match(line) or _HEADING_RE.match(line) or re.match(r"^[（(]?[一二三四五六七八九十]+[)）、]", line):
                    cur = None
                    continue
                if len(cur.text) < 600:
                    cur.text = (cur.text + line).strip()
                    cur.loc.page_end = p
        for c in out:
            c.refs = _refs(c.text)
            c.text = c.text.rstrip(";；。,，")
            c.loc.excerpt = c.text[:300]

    if ch2:
        last_num = ""
        for p, _o, line in doc.iter_lines(range(ch2.page_start, ch2.page_end + 1)):
            if doc.pages[p - 1].is_toc:
                continue
            mn = _NOTICE_NUM_RE.match(line)
            if mn:
                last_num = mn.group(1)
            if _NOTICE_REJECT_RE.search(line) and "有下列情形" not in line:
                cid = f"须知{last_num}" if last_num else f"须知p{p}"
                if any(c.id == cid and c.text == line.strip() for c in out):
                    continue
                out.append(RejectionClause(id=cid, group="须知正文", text=line.strip()[:300],
                                           loc=Location(doc="tender", page=p, section="第2章 投标须知", clause=cid,
                                                        excerpt=line.strip()[:300]),
                                           refs=_refs(line)))
    return out
