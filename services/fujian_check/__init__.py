"""福建施工招标投标检查引擎（fujian_check）。

独立于平台旧的 21 项提示词检查：自建带页码/段落坐标的解析层，
把招标文件的前附表、否决条款、数据表、第八章表单清单抽成结构化数据，
再按规则目录逐条到投标文件里定位证据。
"""
from __future__ import annotations

PARSER_VERSION = "1"
ENGINE_VERSION = "0.2.0"

__all__ = ["PARSER_VERSION", "ENGINE_VERSION"]
