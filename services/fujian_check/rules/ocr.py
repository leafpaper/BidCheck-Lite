"""视觉 OCR：把扫描页渲染成 PNG 交给 qwen-vl，抽证书要素。带磁盘缓存、并发与页数预算。

复用平台 vision_ocr 的模型与端点（qwen-vl-max / DashScope 兼容模式，ALIYUN_API_KEY）。
"""
from __future__ import annotations

import asyncio
import base64
import os
import re

from services.fujian_check import cache
from services.fujian_check.pdf_reader import render_page_png
from services.fujian_check.rules.base import Rule, RuleContext

VISION_MODEL = os.environ.get("FJ_VISION_MODEL", "qwen-vl-max")
VISION_BASE_URL = os.environ.get("FJ_VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

PROMPTS = {
    "cert": (
        "这是一份工程投标文件中的一页扫描件。请判断该页是否包含证书/执照/许可证/凭证/承诺函/授权书等材料。"
        "若包含，请逐条列出可见要素，格式为一行一项：类型|名称|编号|发证机关|有效期(起-止)|持有人或单位|其他关键字（如资质等级、注册专业）。"
        "日期尽量写成 YYYY-MM-DD。看不清的字段写'不清'。若整页无法辨认写'无法辨认'；若页面不含上述材料只回'无证书'。不要编造。"
    ),
    "title": "这是一份投标文件中的一页扫描件。请只输出该页最上方的标题或表单名称一行文字，看不清写'无法辨认'。",
}


def _client():
    from openai import AsyncOpenAI

    key = os.environ.get("ALIYUN_API_KEY", "")
    if not key:
        return None
    return AsyncOpenAI(api_key=key, base_url=VISION_BASE_URL)


async def ocr_pages(pdf_path: str, file_hash: str, pages: list[int], prompt_key: str = "cert", *,
                    concurrency: int = 4, budget: int = 40, stats=None, dpi: int = 110) -> dict[int, str]:
    """返回 {page: text}。缓存命中不计预算；超预算的页跳过并记入 stats.degraded_rules。"""
    out: dict[int, str] = {}
    todo: list[int] = []
    for p in dict.fromkeys(pages):
        cached = cache.ocr_cache_get(file_hash, p, prompt_key)
        if cached is not None:
            out[p] = cached
            if stats:
                stats.cache_hits += 1
        else:
            todo.append(p)
    if not todo:
        return out
    client = _client()
    if client is None:
        return out
    todo = todo[:budget]
    sem = asyncio.Semaphore(concurrency)
    prompt = PROMPTS[prompt_key]

    async def _one(p: int):
        async with sem:
            try:
                png = render_page_png(pdf_path, p, dpi=dpi)
                b64 = base64.b64encode(png).decode()
                resp = await client.chat.completions.create(
                    model=VISION_MODEL,
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]}],
                    max_tokens=700,
                    temperature=0,
                )
                txt = (resp.choices[0].message.content or "").strip()
            except Exception as e:  # 单页失败不阻断
                txt = f"[OCR失败] {type(e).__name__}"
            if not txt.startswith("[OCR失败]"):
                cache.ocr_cache_put(file_hash, p, prompt_key, txt)   # 失败不入缓存，下次可重试
            out[p] = txt
            if stats:
                stats.ocr_pages += 1

    await asyncio.gather(*[_one(p) for p in todo])
    return out


async def prefill_ocr(ctx: RuleContext, rules: list[Rule]) -> None:
    """收集所有 OCR 规则需要的页，去重后一次性识别，写入 ctx.ocr_text。"""
    wanted: list[int] = []
    for r in rules:
        try:
            wanted += r.ocr_pages_needed(ctx)
        except Exception:
            continue
    wanted = [p for p in dict.fromkeys(wanted) if 1 <= p <= ctx.bdoc.n_pages]
    if not wanted:
        return
    if len(wanted) > ctx.ocr_budget:
        ctx.stats.degraded_rules.append(f"OCR 需求 {len(wanted)} 页超过预算 {ctx.ocr_budget}，后 {len(wanted) - ctx.ocr_budget} 页未识别")
    texts = await ocr_pages(ctx.bdoc.path, ctx.bdoc.file_hash, wanted, "cert", budget=ctx.ocr_budget, stats=ctx.stats)
    ctx.ocr_text.update(texts)


# ---------- OCR 文本解析 ----------

_DATE_RE = re.compile(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})")


def dates_in(text: str) -> list[str]:
    return [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in _DATE_RE.findall(text or "")]


def validity_end(text: str) -> str | None:
    """从 OCR 行里取有效期止日：'2024-01-01至2029-01-01' → 2029-01-01；'长期' → '长期'。"""
    if not text:
        return None
    if re.search(r"长期|永久", text):
        return "长期"
    ds = dates_in(text)
    return max(ds) if ds else None


def lines_of_type(text: str, *keywords: str) -> list[str]:
    return [ln for ln in (text or "").split("\n") if any(k in ln for k in keywords)]


def is_unreadable(text: str) -> bool:
    return bool(text) and ("无法辨认" in text or "OCR失败" in text)
