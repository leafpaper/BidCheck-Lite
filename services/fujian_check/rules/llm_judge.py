"""大模型窗口判断：只喂检索窗口，输出结构化 verdict；quote 必须是窗口原文子串，否则降级 manual。"""
from __future__ import annotations

import json
import re
from typing import Any

from services.fujian_check.models import EvidenceWindow, JudgeResult
from services.fujian_check.rules.base import Rule, RuleContext

MAX_INPUT_CHARS = 4000


def _window_text(windows: list[EvidenceWindow]) -> str:
    parts = []
    used = 0
    for w in windows:
        tag = f"[p{w.loc.page}]" if w.loc.page else f"[段落#{w.loc.para_index}]"
        chunk = f"{tag}\n{w.text}"
        if used + len(chunk) > MAX_INPUT_CHARS:
            chunk = chunk[: max(0, MAX_INPUT_CHARS - used)]
        parts.append(chunk)
        used += len(chunk)
        if used >= MAX_INPUT_CHARS:
            break
    return "\n\n".join(parts)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


async def judge_snippet(ctx: RuleContext, rule: Rule, requirement: str, windows: list[EvidenceWindow], *,
                        question: str) -> JudgeResult:
    """返回 JudgeResult；无 LLM / 超预算 / 引用不可验证 → verdict=manual。"""
    if ctx.llm is None or not ctx.use_llm:
        return JudgeResult(verdict="manual", reason="未启用大模型")
    if ctx.stats.llm_calls >= ctx.llm_budget:
        ctx.stats.degraded_rules.append(f"{rule.id}: LLM 调用超预算 {ctx.llm_budget}")
        return JudgeResult(verdict="manual", reason="LLM 预算用尽")
    if not windows:
        return JudgeResult(verdict="manual", reason="未检索到证据窗口")
    evidence = _window_text(windows)
    messages = [
        {"role": "system", "content": (
            "你是福建省房建市政施工招标的评标专家。只根据给出的【投标文件片段】判断是否满足【招标要求】。"
            "不得使用片段之外的信息，不得推测未出现的内容。"
            "返回 JSON：{\"verdict\": \"pass|fail|warning\", \"quote\": \"片段中支持结论的原文（逐字复制，≤120字）\", "
            "\"reason\": \"一句话理由\", \"confidence\": 0~1}。"
            "片段不足以判断时 verdict 用 warning 并说明缺什么。"
        )},
        {"role": "user", "content": f"【招标要求】\n{requirement[:800]}\n\n【判断问题】\n{question}\n\n【投标文件片段】\n{evidence}"},
    ]
    ctx.stats.llm_calls += 1
    try:
        data: dict[str, Any] = await ctx.llm.collect_json(messages=messages, temperature=0.1, max_tokens=600,
                                                          max_repair_attempts=1)
    except Exception as e:
        return JudgeResult(verdict="manual", reason=f"LLM 调用失败: {type(e).__name__}")
    if not isinstance(data, dict):
        return JudgeResult(verdict="manual", reason="LLM 返回非对象")
    verdict = str(data.get("verdict", "warning")).lower()
    if verdict not in ("pass", "fail", "warning"):
        verdict = "warning"
    quote = str(data.get("quote", "") or "")
    reason = str(data.get("reason", "") or "")[:300]
    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    if quote and _norm(quote) not in _norm(evidence):
        return JudgeResult(verdict="manual", quote=quote[:120], reason=f"模型引用无法在片段中核实：{reason}", confidence=0.3)
    return JudgeResult(verdict=verdict, quote=quote[:120], reason=reason, confidence=conf)  # type: ignore[arg-type]


async def extract_kv(ctx: RuleContext, text: str, fields: dict[str, str]) -> dict[str, str | None]:
    """从一段文本抽字段：fields = {字段名: 说明}。无 LLM 返回全 None。"""
    if ctx.llm is None or not ctx.use_llm or ctx.stats.llm_calls >= ctx.llm_budget:
        return {k: None for k in fields}
    ctx.stats.llm_calls += 1
    spec = "\n".join(f"- {k}: {v}" for k, v in fields.items())
    messages = [
        {"role": "system", "content": "从给定文本中抽取字段，找不到的字段填 null，不要推测。只返回 JSON 对象。"},
        {"role": "user", "content": f"字段：\n{spec}\n\n文本：\n{text[:MAX_INPUT_CHARS]}"},
    ]
    try:
        data = await ctx.llm.collect_json(messages=messages, temperature=0.0, max_tokens=500, max_repair_attempts=1)
    except Exception:
        return {k: None for k in fields}
    if not isinstance(data, dict):
        return {k: None for k in fields}
    return {k: (None if data.get(k) in (None, "", "null") else str(data.get(k))) for k in fields}


class FakeLLM:
    """测试用：按顺序返回预置 JSON。"""

    def __init__(self, responses: list[dict]):
        self._r = list(responses)
        self.calls: list[list[dict]] = []

    async def collect_json(self, messages, **kw):
        self.calls.append(messages)
        if not self._r:
            return {}
        r = self._r.pop(0)
        return json.loads(json.dumps(r))
