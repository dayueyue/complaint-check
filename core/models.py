"""ComplaintCheck 数据模型：检查结果、问题项、体检报告。

所有模型可直接 asdict() 序列化为 JSON 返回给前端。
"""
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any

# 三级问题等级
LEVEL_MUST_FIX = "must_fix"          # 必须修改
LEVEL_TO_CONFIRM = "to_confirm"      # 待确认
LEVEL_SUGGESTION = "suggestion"      # 建议

LEVEL_LABELS = {
    LEVEL_MUST_FIX: "必须修改",
    LEVEL_TO_CONFIRM: "待确认",
    LEVEL_SUGGESTION: "建议",
}

LEVEL_SCORE = {
    LEVEL_MUST_FIX: 15,   # 每项扣 15 分
    LEVEL_TO_CONFIRM: 5,  # 每项扣 5 分
    LEVEL_SUGGESTION: 0,  # 不计分
}

DISCLAIMER = "本报告仅供学习参考，不构成法律意见，正式文书请咨询执业律师。"


@dataclass
class Issue:
    """单条检查未通过产生的问题项。"""
    rule_id: str          # 如 A1 / B3 / D-J1
    dimension: str        # A/B/C/E/F/D
    title: str            # 检查项名称
    level: str            # must_fix / to_confirm / suggestion
    level_label: str      # 必须修改 / 待确认 / 建议
    section: str          # 命中的文书部分（title/plaintiff/.../whole）
    message: str          # 问题描述（哪里有问题）
    advice: str           # 修改建议（怎么改）
    basis: str            # 法条/依据
    matched: List[str] = field(default_factory=list)  # 命中的原文片段（用于高亮）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CheckReport:
    """一次完整检查的体检报告。"""
    case_type: str        # 案由 key：general / jiamin / maimai / qinquan
    case_label: str       # 案由显示名
    total_rules: int      # 本次适用的检查项总数
    passed: int           # 通过的检查项数
    score: int            # 立案风险分 0-100
    grade: str            # 结论文案
    issues: List[Issue] = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def make_report(case_type: str, case_label: str,
                total_rules: int, issues: List[Issue]) -> CheckReport:
    """根据问题清单计算得分并生成报告。"""
    must = sum(1 for i in issues if i.level == LEVEL_MUST_FIX)
    confirm = sum(1 for i in issues if i.level == LEVEL_TO_CONFIRM)
    score = max(0, 100 - must * LEVEL_SCORE[LEVEL_MUST_FIX]
                - confirm * LEVEL_SCORE[LEVEL_TO_CONFIRM])
    if score < 60:
        grade = "立案风险较高：建议修改后再提交立案"
    elif must > 0 or confirm > 0:
        grade = "存在风险项：建议逐项核查后提交立案"
    else:
        grade = "未发现问题：可提交立案（仅供学习参考）"
    return CheckReport(
        case_type=case_type,
        case_label=case_label,
        total_rules=total_rules,
        passed=total_rules - len(issues),
        score=score,
        grade=grade,
        issues=issues,
    )
