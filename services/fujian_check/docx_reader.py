"""docx 投标文件读取：按 body 顺序遍历段落与表格，合成"页"（每个一级标题一页）。

Location.page 对 docx 为合成页号（同样可以定位到"第几个一级标题下"），para_index 为段落序号，
heading_path 为标题路径。表格进 ParsedDoc.tables（page = 合成页号）。
"""
from __future__ import annotations

import re
from pathlib import Path

from services.fujian_check import PARSER_VERSION
from services.fujian_check.models import Block, Page, ParsedDoc, Table
from services.fujian_check.pdf_reader import file_sha256
from services.fujian_check.textnorm import normalize_line

_TOP_HEADING_RE = re.compile(r"^(第\s*\d+\s*[章节]|第[一二三四五六七八九十]+[章节]|[一二三四五六七八九十]{1,3}、)")
_SUB_HEADING_RE = re.compile(r"^(（[一二三四五六七八九十]+）|\([一二三四五六七八九十]+\)|\d+[、.])")
# 这些行也另起一个"合成页"，让按页工作的招标解析器（前附表/专项附件/附件清单）在 Word 上也能分段
_PAGE_BREAK_RE = re.compile(r"^(专项附件|附件\s*\d|附表\s*[一二三四五六七八九十\d]|投标须知前附表|磋商须知前附表|评标办法)")
_CH_TITLE_RE = re.compile(r"^第\s*([一二三四五六七八九十]+|\d+)\s*章")
_TOC_LINE_RE = re.compile(r"[.…·]{3,}\s*\d+\s*$")


def parse_docx(path: str | Path) -> ParsedDoc:
    from docx import Document
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph

    path = str(path)
    doc = Document(path)
    pages: list[Page] = []
    tables: list[Table] = []
    cur_blocks: list[Block] = []
    heading_path: list[str] = []
    order = 0
    para_index = 0
    page_no = 1

    def flush():
        nonlocal cur_blocks, page_no
        if cur_blocks:
            text = "\n".join(b.text for b in cur_blocks)
            n_ch = sum(1 for b in cur_blocks if _CH_TITLE_RE.match(b.text))
            n_leader = sum(1 for b in cur_blocks if _TOC_LINE_RE.search(b.text))
            is_toc = n_ch >= 5 or (len(cur_blocks) >= 8 and n_leader >= len(cur_blocks) * 0.6)
            pages.append(Page(page=page_no, char_count=len(text), blocks=cur_blocks, is_toc=is_toc))
            page_no += 1
            cur_blocks = []

    def only_headings() -> bool:
        """当前合成页是否只有标题/目录行（还没有正文）——连续的目录行应留在同一页，才能被判成目录页。"""
        return bool(cur_blocks) and all(b.kind == "heading" or _PAGE_BREAK_RE.match(b.text) for b in cur_blocks)

    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            text = normalize_line(para.text)
            para_index += 1
            if not text:
                continue
            style = (para.style.name or "").lower() if para.style is not None else ""
            is_heading = "heading" in style or "标题" in style or bool(_TOP_HEADING_RE.match(text))
            level = 1
            if is_heading:
                m = re.search(r"(\d)", style)
                if m:
                    level = int(m.group(1))
                elif _SUB_HEADING_RE.match(text):
                    level = 2
                if level <= 1 and _TOP_HEADING_RE.match(text):
                    # 目录页里的标题行连在一起不分页；正文里再次出现同名标题（目录之后的真标题）才分页
                    if not only_headings() or any(b.text == text for b in cur_blocks):
                        flush()
                    heading_path = [text]
                else:
                    heading_path = heading_path[:1] + [text]
            elif _PAGE_BREAK_RE.match(text):
                if not only_headings() or any(b.text == text for b in cur_blocks):
                    flush()
            cur_blocks.append(Block(page=None, order=order, text=text, kind="heading" if is_heading else "text",
                                    para_index=para_index, heading_path=list(heading_path)))
            order += 1
        elif tag == "tbl":
            tbl = DocxTable(child, doc)
            rows: list[list[str]] = []
            for row in tbl.rows:
                cells = [normalize_line(c.text) for c in row.cells]
                # 合并单元格会重复出现，去掉连续重复
                dedup: list[str] = []
                for c in cells:
                    if not dedup or dedup[-1] != c:
                        dedup.append(c)
                if any(dedup):
                    rows.append(dedup)
            if rows:
                tables.append(Table(page=page_no, index=len(tables), rows=rows))
                flat = "\n".join(" | ".join(r) for r in rows)
                para_index += 1
                cur_blocks.append(Block(page=None, order=order, text=flat, kind="table_cell",
                                        para_index=para_index, heading_path=list(heading_path)))
                order += 1
                flush()   # 表格与其前面的标题/说明同页，表格之后另起一页（按页取表的解析器只看标题所在页）
    flush()
    # 合成页号写回 Block.page，便于统一按页定位
    for pg in pages:
        for b in pg.blocks:
            b.page = pg.page
    return ParsedDoc(path=path, file_hash=file_sha256(path), kind="docx", parser_version=PARSER_VERSION,
                     n_pages=len(pages), pages=pages, tables=tables)
