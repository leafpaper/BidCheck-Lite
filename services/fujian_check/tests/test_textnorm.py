from services.fujian_check import textnorm as t


def test_dedupe_doubled():
    assert t.dedupe_doubled("条条款款名名称称") == "条款名称"
    assert t.dedupe_doubled("谢谢") == "谢谢"
    assert t.dedupe_doubled("叁叁") == "叁"
    assert t.dedupe_doubled_runs("提提交交电电汇汇或或银银行行转账") == "提交电汇或银行转账"
    assert t.dedupe_doubled_runs("正常文本哈哈不变") == "正常文本哈哈不变"


def test_collapse_vertical():
    assert t.collapse_vertical(["缺", "陷", "责", "任", "期", "24个月"]) == ["缺陷责任期", "24个月"]
    assert t.collapse_vertical(["合", "计"]) == ["合", "计"]


def test_cn_money():
    assert t.cn_money_to_int("叁仟壹佰柒拾万捌仟柒佰叁拾玖元整") == 31708739
    assert t.cn_money_to_int("叁拾肆万元整") == 340000
    assert t.cn_money_to_int("壹亿零柒拾捌万元") == 100780000
    assert t.cn_money_to_int("壹佰元伍角") == 101


def test_money():
    assert t.money("34,652,556元") == 34652556
    assert t.money("34.65万元") == 346500
    assert t.money("1万") == 10000
    assert t.money("340000 元") == 340000
    assert t.money("无") is None


def test_cn_to_int():
    assert t.cn_to_int("十二") == 12
    assert t.cn_to_int("二十") == 20
    assert t.cn_to_int("二十一") == 21
    assert t.cn_to_int("三") == 3
    assert t.cn_to_int("abc") is None
