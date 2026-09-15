"""profile 检测：在前 60 页统计各模板锚点命中数。"""
from __future__ import annotations

import re

from services.fujian_check.models import ParsedDoc, TenderProfile
from services.fujian_check.profiles.base import Profile
from services.fujian_check.profiles.fujian_gov_cs import FUJIAN_GOV_CS
from services.fujian_check.profiles.fujian_platform import FUJIAN_PLATFORM
from services.fujian_check.profiles.jiubuwei import JIUBUWEI

PROFILES: dict[str, Profile] = {p.key: p for p in (FUJIAN_PLATFORM, FUJIAN_GOV_CS, JIUBUWEI)}
DETECT_PAGES = 60
MIN_CONFIDENCE = 0.4


def get_profile(key: str) -> Profile:
    return PROFILES.get(key, FUJIAN_PLATFORM)


def _head_text(doc: ParsedDoc, n: int = DETECT_PAGES) -> str:
    return "\n".join(doc.page_text(p) for p in range(1, min(n, doc.n_pages) + 1))


def detect_profile(doc: ParsedDoc) -> TenderProfile:
    text = _head_text(doc)
    best: tuple[float, Profile | None, list[str]] = (0.0, None, [])
    for prof in PROFILES.values():
        hits = [a for a in prof.anchors if re.search(a, text)]
        conf = len(hits) / max(len(prof.anchors), 1)
        if conf > best[0]:
            best = (conf, prof, hits)
    conf, prof, hits = best
    if prof is None or conf < MIN_CONFIDENCE:
        return TenderProfile(key="unknown", confidence=round(conf, 2), evidence=hits)
    full = "\n".join(doc.page_text(p) for p in range(1, doc.n_pages + 1))
    variant = {name: bool(re.search(pat, full)) for name, pat in prof.variant_patterns.items()}
    return TenderProfile(key=prof.key, confidence=round(conf, 2), evidence=hits, variant=variant)  # type: ignore[arg-type]
