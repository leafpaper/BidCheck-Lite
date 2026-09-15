"""FJ-E 电子标：PDF 无法判定，输出人工自查项并给出招标依据位置。"""
from __future__ import annotations

from services.fujian_check.models import Finding
from services.fujian_check.rules.base import Rule, RuleContext

GROUP = "电子标"


class E01Xml(Rule):
    id, group, title, severity = "FJ-E-01", GROUP, "XML 电子清单已随附并与 PDF 一致", "reject"
    method = "manual"
    supersedes = ["ebidSubmit"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        if not ctx.req.hard.xml_required:
            return [self.na("招标未要求 XML")]
        return [self.manual("商务文件须含已标价工程量清单 XML 与 PDF，且两者一致（须知 20.1/20.5）",
                            "请在电子标书生成器中核对", [], ctx.qfb_loc("20.1"))]


class E02Ca(Rule):
    id, group, title, severity = "FJ-E-02", GROUP, "本单位企业数字证书加密与签章", "reject"
    method = "manual"
    supersedes = ["signature", "ebidSubmit"]

    async def run(self, ctx: RuleContext) -> list[Finding]:
        return [self.manual("投标文件须用本单位企业 CA 加密；解密时长内在线解密（须知 20.5/22.2）",
                            "请核对 CA 归属与有效期", [], ctx.qfb_loc("20.5"))]


class E03SoftwareInfo(Rule):
    id, group, title, severity = "FJ-E-03", GROUP, "XML 软硬件信息记录（须知 20.7）", "reject"
    method = "manual"

    async def run(self, ctx: RuleContext) -> list[Finding]:
        return [self.manual("已标价清单 XML 须记录编制软件名称版本、硬件信息等（20.7 四项要求）", "请在生成器中确认", [], ctx.qfb_loc("20.5"))]


RULES = [E01Xml, E02Ca, E03SoftwareInfo]
