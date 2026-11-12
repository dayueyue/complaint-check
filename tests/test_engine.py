# -*- coding: utf-8 -*-
"""规则引擎与 API 测试。"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.engine import RuleEngine  # noqa: E402
from core.models import LEVEL_MUST_FIX, LEVEL_TO_CONFIRM, LEVEL_SUGGESTION  # noqa: E402

ENGINE = RuleEngine(BASE / "rules")

GOOD_JIAMIN = (BASE / "samples" / "sample_1_民间借贷.txt").read_text(encoding="utf-8")
GOOD_MAIMAI = (BASE / "samples" / "sample_2_买卖合同.txt").read_text(encoding="utf-8")

BAD_TEXT = """欠条

原告张三
被告待查

诉讼请求

要求被告赔偿损失。

事实与理由

事情是这样的：被告欠钱不还，民法典188条有规定。
"""


def test_rule_count():
    """通用 22 项 + 3 个案由共 13 项专项 = 35。"""
    assert len(ENGINE.common_rules) == 22
    total_case = sum(len(p["rules"]) for p in ENGINE.case_rules.values())
    assert total_case == 13
    assert len(ENGINE.all_rules()) == 35


def test_case_options():
    keys = {c["key"] for c in ENGINE.case_options}
    assert keys == {"general", "jiamin", "maimai", "qinquan"}


def test_good_jiamin_sample():
    """示例一（民间借贷）：利率月息2分=年化24%，超 LPR 四倍 → 仅 D-J3 触发必须修改。"""
    report = ENGINE.run(GOOD_JIAMIN, "jiamin")
    failing = {i["rule_id"] for i in report["issues"]}
    unexpected = failing - {"D-J3", "F2"}
    assert not unexpected, f"意外未通过项：{unexpected}"
    assert report["score"] == 85  # 100 - 15（D-J3 必须修改）
    d_j3 = next(i for i in report["issues"] if i["rule_id"] == "D-J3")
    assert d_j3["level"] == LEVEL_MUST_FIX


def test_good_maimai_sample_clean():
    """示例二（买卖合同）应全部通过，得分 100。"""
    report = ENGINE.run(GOOD_MAIMAI, "maimai")
    failing = [i["rule_id"] for i in report["issues"] if i["rule_id"] != "F2"]
    assert report["score"] == 100, f"未通过项：{failing}"


def test_bad_text_triggers_key_rules():
    report = ENGINE.run(BAD_TEXT, "jiamin")
    ids = {i["rule_id"] for i in report["issues"]}
    # 标题、被告明确性、金钱数额、事实段金额、法条格式、请求项编号等应触发
    for rid in ("A1", "B3", "C1", "C2", "E2", "E3"):
        assert rid in ids, f"{rid} 未触发"
    # E3 因"民法典188条"格式非法 → 必须修改
    e3 = next(i for i in report["issues"] if i["rule_id"] == "E3")
    assert e3["level"] == LEVEL_MUST_FIX


def test_scoring():
    """必须修改 -15、待确认 -5、建议不计分。"""
    report = ENGINE.run(BAD_TEXT, "general")
    must = sum(1 for i in report["issues"] if i["level"] == LEVEL_MUST_FIX)
    confirm = sum(1 for i in report["issues"] if i["level"] == LEVEL_TO_CONFIRM)
    suggest = sum(1 for i in report["issues"] if i["level"] == LEVEL_SUGGESTION)
    assert report["score"] == max(0, 100 - must * 15 - confirm * 5)
    assert suggest >= 1  # C4 诉讼费 / F2 管辖提示卡至少触发其一


def test_rate_lpr4():
    """月息2分 = 年化24% > 12% 上限 → 必须修改。"""
    text = GOOD_JIAMIN.replace("月息2分", "月息2分")
    report = ENGINE.run(text, "jiamin")
    d_j3 = [i for i in report["issues"] if i["rule_id"] == "D-J3"]
    assert len(d_j3) == 1 and d_j3[0]["level"] == LEVEL_MUST_FIX


def test_rate_within_limit_passes():
    text = GOOD_JIAMIN.replace("月息2分", "年息1分")
    report = ENGINE.run(text, "jiamin")
    assert all(i["rule_id"] != "D-J3" for i in report["issues"])


def test_no_citation_is_suggestion():
    """完全无法条引用 → 建议（非必须修改）。"""
    text = GOOD_JIAMIN.replace("依据《民法典》第667条、第675条、第676条之规定", "")
    text = text.replace("依据《民事诉讼法》第122条之规定", "")
    report = ENGINE.run(text, "jiamin")
    e3 = [i for i in report["issues"] if i["rule_id"] == "E3"]
    assert len(e3) == 1 and e3[0]["level"] == LEVEL_SUGGESTION


def test_unknown_case():
    with pytest.raises(ValueError):
        ENGINE.run(GOOD_JIAMIN, "not_exist")


def test_determinism():
    """同一输入重复检查 10 次，输出完全一致。"""
    results = [ENGINE.run(GOOD_MAIMAI, "maimai") for _ in range(10)]
    assert all(r == results[0] for r in results)
