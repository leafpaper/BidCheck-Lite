"""PDF 读取：PyMuPDF 逐页文本块（带 bbox 与顺序），pdfplumber 只对指定页抽表。

设计要点（来自黄金样本核实）：
- 福建平台导出 PDF 文字层完好；PyMuPDF get_text 字形不双写，pdfplumber 加粗会双写 → 文本以 PyMuPDF 为准。
- 招标文件页脚恒为 "N / 总页数"，剥掉不进正文。
- 扫描页 = 文字 < 30 字且含图片。
- 目录页 = 同页出现 ≥5 个"第X章"标题或 ≥8 行"……数字"目录行。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from services.fujian_check import PARSER_VERSION
from services.fujian_check.models import Block, Page, ParsedDoc, Table
from services.fujian_check.textnorm import collapse_vertical, normalize_line

_FOOTER_RE = re.compile(r"^\s*(\d{1,4})\s*/\s*(\d{1,4})\s*$")
_CHAPTER_RE = re.compile(r"第\s*([一二三四五六七八九十]|\d{1,2})\s*章")
_TOC_LINE_RE = re.compile(r"(…{2,}|\.{4,}|·{3,})\s*\(?\d{1,4}\)?\s*$")
SCANNED_CHAR_THRESHOLD = 30


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_toc_page(lines: list[str]) -> bool:
    chapter_hits = sum(1 for ln in lines if _CHAPTER_RE.search(ln))
    toc_lines = sum(1 for ln in lines if _TOC_LINE_RE.search(ln))
    return chapter_hits >= 5 or toc_lines >= 8


def parse_pdf(path: str | Path, *, table_pages: set[int] | None = None) -> ParsedDoc:
    import fitz  # PyMuPDF

    path = str(path)
    doc = fitz.open(path)
    pages: list[Page] = []
    order = 0
    n = len(doc)
    warnings: list[str] = []
    for i in range(n):
        pg = doc[i]
        pno = i + 1
        raw_blocks = pg.get_text("blocks")  # (x0,y0,x1,y1,text,block_no,block_type)
        raw_blocks.sort(key=lambda b: (round(b[1], 1), round(b[0], 1)))
        blocks: list[Block] = []
        footer: str | None = None
        char_count = 0
        for b in raw_blocks:
            if b[6] != 0:  # image block
                continue
            text = b[4]
            lines = [normalize_line(ln) for ln in text.split("\n")]
            lines = [ln for ln in lines if ln]
            if not lines:
                continue
            # 页脚 "N / total"
            if len(lines) == 1 and _FOOTER_RE.match(lines[0]):
                m = _FOOTER_RE.match(lines[0])
                if m and int(m.group(2)) == n:
                    footer = lines[0]
                    continue
            lines = collapse_vertical(lines)
            joined = "\n".join(lines)
            char_count += len(joined)
            blocks.append(
                Block(page=pno, order=order, bbox=(b[0], b[1], b[2], b[3]), text=joined)
            )
            order += 1
        has_image = any(b[6] == 1 for b in raw_blocks) or bool(pg.get_images(full=False))
        is_scanned = char_count < SCANNED_CHAR_THRESHOLD and has_image
        all_lines = [ln for blk in blocks for ln in blk.text.split("\n")]
        pages.append(
            Page(
                page=pno,
                char_count=char_count,
                is_scanned=is_scanned,
                is_toc=_is_toc_page(all_lines),
                footer_stripped=footer,
                blocks=blocks,
            )
        )
    doc.close()

    tables: list[Table] = []
    if table_pages:
        for pno, tbls in extract_tables(path, sorted(table_pages)).items():
            for idx, rows in enumerate(tbls):
                tables.append(Table(page=pno, index=idx, rows=rows))

    return ParsedDoc(
        path=path,
        file_hash=file_sha256(path),
        kind="pdf",
        parser_version=PARSER_VERSION,
        n_pages=n,
        pages=pages,
        tables=tables,
        parse_warnings=warnings,
    )


def extract_tables(path: str | Path, pages: list[int]) -> dict[int, list[list[list[str]]]]:
    """pdfplumber 表格抽取，仅指定页。单元格做双写去重与换行折叠。"""
    import pdfplumber

    from services.fujian_check.textnorm import dedupe_doubled_in_line, dedupe_doubled_runs

    out: dict[int, list[list[list[str]]]] = {}
    with pdfplumber.open(str(path)) as pdf:
        for pno in pages:
            if not (1 <= pno <= len(pdf.pages)):
                continue
            page = pdf.pages[pno - 1]
            try:
                raw_tables = page.extract_tables() or []
            except Exception:  # pragma: no cover - pdfplumber 偶发
                raw_tables = []
            cleaned: list[list[list[str]]] = []
            for tb in raw_tables:
                rows: list[list[str]] = []
                for row in tb:
                    cells: list[str] = []
                    for cell in row:
                        txt = cell or ""
                        lines = [normalize_line(x) for x in txt.split("\n")]
                        lines = [dedupe_doubled_runs(dedupe_doubled_in_line(x)) for x in lines if x]
                        lines = collapse_vertical(lines)
                        cells.append(" ".join(lines).strip())
                    if any(cells):
                        rows.append(cells)
                if rows:
                    cleaned.append(rows)
            out[pno] = cleaned
    return out


def render_page_png(path: str | Path, page: int, dpi: int = 100) -> bytes:
    """渲染单页为 PNG 字节（供视觉 OCR）。"""
    import fitz

    doc = fitz.open(str(path))
    try:
        pix = doc[page - 1].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    finally:
        doc.close()
