"""模板 profile 协议：锚点、同义词、表单别名、否决条款编号规则。

新增地区/平台模板 = 新增一个 Profile 实例（数据），核心解析逻辑读 profile 而不写死字面。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Profile:
    key: str
    display_name: str
    # 检测锚点（正则，前 60 页统计命中）
    anchors: list[str]
    # 变体检测：名称 → 正则（命中即 True）
    variant_patterns: dict[str, str] = field(default_factory=dict)
    # 概念 → 同义写法（用于定位）
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    # 第8章表单标题别名：规范标题 → 投标文件里可能的写法
    form_aliases: dict[str, list[str]] = field(default_factory=dict)
    # 标题白名单里"如有时"类可选表单关键词
    optional_form_markers: list[str] = field(default_factory=lambda: ["如有时", "如有", "若有"])
    # 否决条款编号前缀 → 分组
    rejection_groups: dict[str, str] = field(default_factory=dict)
    # 投标文件节标题 → 规范节名
    bid_section_patterns: dict[str, str] = field(default_factory=dict)

    def syn(self, concept: str) -> list[str]:
        return self.synonyms.get(concept, [concept])

    def syn_regex(self, concept: str) -> str:
        return "|".join(self.syn(concept))
