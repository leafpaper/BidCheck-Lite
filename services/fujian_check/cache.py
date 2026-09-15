"""磁盘缓存：解析结果 / OCR 文本 / 表格。

key = sha256(文件)[:16] + 版本号。目录由 settings.fj_cache_dir 决定（默认 ./uploads/fujian_cache）。
任何缓存失败都静默降级为"无缓存"。
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

from services.fujian_check import PARSER_VERSION
from services.fujian_check.models import ParsedDoc

OCR_PROMPT_VERSION = "1"


def cache_dir() -> Path:
    env = os.environ.get("FJ_CACHE_DIR")
    if env:
        return Path(env)
    try:
        from core.settings import get_settings

        d = getattr(get_settings(), "fj_cache_dir", None)
        if d:
            return Path(d)
    except Exception:
        pass
    return Path("./uploads/fujian_cache")


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _short(file_hash: str) -> str:
    return file_hash[:16]


# ---------- ParsedDoc ----------

def parsed_path(file_hash: str) -> Path:
    return _ensure(cache_dir() / "parsed") / f"{_short(file_hash)}_{PARSER_VERSION}.json.gz"


def load_parsed(file_hash: str) -> ParsedDoc | None:
    p = parsed_path(file_hash)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return ParsedDoc.model_validate_json(f.read())
    except Exception:
        return None


def save_parsed(doc: ParsedDoc) -> None:
    try:
        p = parsed_path(doc.file_hash)
        with gzip.open(p, "wt", encoding="utf-8") as f:
            f.write(doc.model_dump_json())
    except Exception:
        pass


# ---------- OCR ----------

def _ocr_path(file_hash: str, page: int, prompt_key: str) -> Path:
    return _ensure(cache_dir() / "ocr" / _short(file_hash)) / f"{page}_{prompt_key}_{OCR_PROMPT_VERSION}.txt"


def ocr_cache_get(file_hash: str, page: int, prompt_key: str) -> str | None:
    p = _ocr_path(file_hash, page, prompt_key)
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None
    return None


def ocr_cache_put(file_hash: str, page: int, prompt_key: str, text: str) -> None:
    try:
        _ocr_path(file_hash, page, prompt_key).write_text(text, encoding="utf-8")
    except Exception:
        pass


# ---------- 表格 ----------

def _tables_path(file_hash: str, page: int) -> Path:
    return _ensure(cache_dir() / "tables" / _short(file_hash)) / f"{page}.json"


def tables_cache_get(file_hash: str, page: int) -> list[list[list[str]]] | None:
    p = _tables_path(file_hash, page)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def tables_cache_put(file_hash: str, page: int, tables: list[list[list[str]]]) -> None:
    try:
        _tables_path(file_hash, page).write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
