"""页面感知哈希（8×8 平均哈希），用于扫描页重复检测。仅依赖 PyMuPDF。"""
from __future__ import annotations


def page_hash(pdf_path: str, page: int, size: int = 8) -> int:
    import fitz

    doc = fitz.open(pdf_path)
    try:
        pg = doc[page - 1]
        # 渲染成小灰度图（宽 ~64px）后再缩到 size×size
        zoom = 64.0 / max(pg.rect.width, 1)
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
        w, h, buf = pix.width, pix.height, pix.samples
        cells = [[0.0] * size for _ in range(size)]
        counts = [[0] * size for _ in range(size)]
        for y in range(h):
            cy = min(size - 1, y * size // max(h, 1))
            row = y * w
            for x in range(w):
                cx = min(size - 1, x * size // max(w, 1))
                cells[cy][cx] += buf[row + x]
                counts[cy][cx] += 1
        vals = [cells[y][x] / max(counts[y][x], 1) for y in range(size) for x in range(size)]
        avg = sum(vals) / len(vals)
        bits = 0
        for v in vals:
            bits = (bits << 1) | (1 if v > avg else 0)
        return bits
    finally:
        doc.close()


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
