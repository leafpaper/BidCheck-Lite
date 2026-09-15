"""九部委通用版（《标准施工招标文件》体例，如福州外立面改造样本）。

Phase 5 再实现完整解析；此处先放锚点常量供 detect_profile 区分模板家族。
"""
from __future__ import annotations

from services.fujian_check.profiles.base import Profile

JIUBUWEI = Profile(
    key="jiubuwei",
    display_name="九部委《标准施工招标文件》通用版",
    anchors=[
        r"投标人须知前附表",
        r"评标办法前附表",
        r"无效投标条件",
        r"附件A",
        r"资格审查资料",
        r"最高投标限价",
        r"第八章\s*投标文件格式|第\s*8\s*章\s*投标文件格式",
        r"投标函附录",
    ],
    variant_patterns={
        "暗标": r"暗标",
        "联合体": r"接受联合体",
        "电子标": r"电子投标文件|计算机辅助评标",
    },
    synonyms={
        "前附表": ["投标人须知前附表"],
        "数据表": ["评标办法前附表"],
        "否决": ["无效投标", "否决投标", "废标"],
        "控制价": ["最高投标限价", "招标控制价"],
        "资格审查": ["资格审查资料"],
    },
    rejection_groups={"A1": "其他"},
)
