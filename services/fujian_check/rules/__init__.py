"""规则注册表：按 profile 装载各分组规则。"""
from __future__ import annotations

from importlib import import_module

from services.fujian_check.rules.base import Rule

_GROUP_MODULES = [
    "group_q", "group_b", "group_d", "group_t", "group_p", "group_x", "group_f", "group_s", "group_e", "group_c",
    "group_ocr_q", "group_llm", "group_gc",
]
DEFAULT_PROFILES = {"fujian_platform", "jiubuwei", "unknown"}


def load_rules(profile_key: str = "fujian_platform", rule_ids: list[str] | None = None) -> list[Rule]:
    rules: list[Rule] = []
    for name in _GROUP_MODULES:
        try:
            mod = import_module(f"services.fujian_check.rules.{name}")
        except ModuleNotFoundError as e:
            if e.name == f"services.fujian_check.rules.{name}":
                continue
            raise   # 规则模块自身缺依赖不能静默吞掉（打包漏包时要能看到）
        allowed = getattr(mod, "PROFILES", DEFAULT_PROFILES)
        if profile_key not in allowed:
            continue
        for r in getattr(mod, "RULES", []):
            rules.append(r() if isinstance(r, type) else r)
    if rule_ids:
        wanted = {x.upper() for x in rule_ids}
        rules = [r for r in rules if r.id.upper() in wanted]
    return rules
