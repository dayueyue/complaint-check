"""规则执行引擎：加载 YAML 规则库，逐条执行，汇总为体检报告。

设计要点（对应 PRD 5.5 / 六-可扩展性）：
- 引擎与规则解耦：新增案由只需在 rules/cases/ 增加一个 YAML 文件
- 声明式检查类型：contains_any / contains_all / missing_all / regex_any / regex_all
- 复杂逻辑走 custom 处理器（engine 内注册，规则里写 check: custom:<name>）
- 所有检查为确定性代码：同一输入永远同一输出
"""
import re
from pathlib import Path
from typing import Dict, List, Tuple, Callable, Any

import yaml

from .models import Issue, LEVEL_LABELS, make_report
from .segmenter import split_sections, get_section

# 常用正则片段
RE_AMOUNT = re.compile(r"\d[\d,，]*\.?\d*\s*(?:万)?元|人民币\s*[\d,，]+")
RE_DATE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月|\d{4}\d{2}\d{2}")
RE_PHONE = re.compile(r"1[3-9]\d{9}|0\d{2,3}[-—]?\d{7,8}")
RE_ID_NUMBER = re.compile(r"\d{17}[0-9Xx]|\d{15}")
RE_USCC = re.compile(r"[0-9A-HJ-NP-RT-UW-Y]{2}\d{6}[0-9A-HJ-NP-RT-UW-Y]{10}")
RE_LEGAL_CITE = re.compile(r"《[^》]{1,25}》\s*第[0-9一二三四五六七八九十百千]+条")
RE_ILLEGAL_CITE = re.compile(r"(?:民法典|民事诉讼法|刑法)\d+条")
RE_RATE = re.compile(r"年利率|月利率|LPR|利息按.{0,6}计算|\d+\s*%|月息.{0,6}分|年息.{0,6}分")
RE_START_DATE = re.compile(r"自\s*.{0,15}?(?:之日起|起)|起算")


def _join(sections: Dict[str, str], *keys: str) -> str:
    return "\n".join(sections.get(k, "") for k in keys)


# ---------------- 复杂规则的自定义处理器 ----------------
# 签名：handler(text, sections, rule) -> (passed, matched, detail[, level_override])

def h_money_amount(text, sections, rule):
    """C2 / D-Q3：金钱请求有明确数额。"""
    seg = _join(sections, "claims")
    verbs = re.findall(r"赔偿|返还|支付|偿还|退还", seg)
    if not verbs:
        return True, [], ""
    amounts = RE_AMOUNT.findall(seg)
    if amounts:
        return True, amounts[:3], ""
    return False, verbs[:2], "检出金钱给付类表述但未见明确数额"


def h_facts_amount(text, sections, rule):
    """E2：金钱类请求对应事实段应含金额。"""
    claims = sections.get("claims", "")
    if not re.search(r"赔偿|返还|支付|偿还|退还", claims):
        return True, [], ""
    if RE_AMOUNT.search(_join(sections, "facts")):
        return True, [], ""
    return False, [], "诉讼请求涉及金钱给付，但事实与理由段未见对应金额"


def h_interest_complete(text, sections, rule):
    """C3：利息请求完整性（利率标准 + 起算日期）。"""
    seg = _join(sections, "claims", "facts")
    if not re.search(r"利息|月息|年息|利率", seg):
        return True, [], ""
    missing = []
    if not RE_RATE.search(seg):
        missing.append("利率标准（如 LPR 四倍、年利率 X%）")
    if not RE_START_DATE.search(seg):
        missing.append("起算日期（自 X 年 X 月 X 日起）")
    if missing:
        return False, ["利息"], "利息请求缺少：" + "、".join(missing)
    return True, [], ""


def h_citation_format(text, sections, rule):
    """E3：理由段法条引用格式。第 4 个返回值用于动态分级。"""
    seg = _join(sections, "facts")
    illegal = RE_ILLEGAL_CITE.findall(seg)
    if illegal:
        return False, illegal[:3], "法条引用格式不规范（缺少书名号，如\"民法典577条\"）", "must_fix"
    legal = RE_LEGAL_CITE.findall(seg)
    if legal:
        return True, legal[:3], ""
    return False, [], "事实与理由段完全没有法条引用，建议补充请求权基础条文", "suggestion"


def h_plaintiff_id(text, sections, rule):
    """B2：原告身份信息（自然人需身份证号或性别+出生日期；法人需信用代码）。"""
    seg = _join(sections, "plaintiff")
    if RE_USCC.search(seg) or RE_ID_NUMBER.search(seg):
        return True, [], ""
    has_sex = bool(re.search(r"(?<!被)原告[^。]{0,30}(男|女)", seg)) or ("性别" in seg)
    has_birth = bool(re.search(r"出生|\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", seg))
    if has_sex and has_birth:
        return True, [], ""
    return False, [], "原告身份信息不完整（自然人需性别+出生日期或身份证号；法人需统一社会信用代码）"


def h_defendant_sufficient(text, sections, rule):
    """B4：被告身份信息足以送达（地址 + 电话/身份证/信用代码任一）。"""
    seg = _join(sections, "defendant")
    if not seg.strip():
        return False, [], "被告信息段为空"
    has_addr = bool(re.search(r"住址|住所地|地址|户籍|居住|住\s*\S{0,12}(?:市|省|县|区)|住所", seg))
    has_contact = bool(RE_PHONE.search(seg) or RE_ID_NUMBER.search(seg)
                       or RE_USCC.search(seg))
    if has_addr and has_contact:
        return True, [], ""
    missing = []
    if not has_addr:
        missing.append("住址/住所地")
    if not has_contact:
        missing.append("电话或身份证号")
    return False, [], "被告信息不足以送达，缺少：" + "、".join(missing)


def h_rate_lpr4(text, sections, rule):
    """D-J3：利率约定是否超过 LPR 四倍（阈值可配，默认年化 12%）。"""
    seg = _join(sections, "claims", "facts")
    threshold = float((rule.get("params") or {}).get("lpr4_annual", 12.0))
    exceeded = []
    for m in re.finditer(r"月息\s*(\d+(?:\.\d+)?)\s*分", seg):        # 月息X分 → 年化 X*12 %
        annual = float(m.group(1)) * 12
        if annual > threshold:
            exceeded.append((m.group(0), f"月息{m.group(1)}分，年化约{annual:.0f}%"))
    for m in re.finditer(r"年息\s*(\d+(?:\.\d+)?)\s*分", seg):        # 年息X分 → 年利率 X%
        annual = float(m.group(1))
        if annual > threshold:
            exceeded.append((m.group(0), f"年息{m.group(1)}分，年化约{annual:.0f}%"))
    for m in re.finditer(r"月利率\s*(\d+(?:\.\d+)?)\s*%", seg):       # 月利率X% → 年化 X*12
        annual = float(m.group(1)) * 12
        if annual > threshold:
            exceeded.append((m.group(0), f"月利率{m.group(1)}%，年化约{annual:.0f}%"))
    for m in re.finditer(r"年利率\s*(\d+(?:\.\d+)?)\s*%", seg):       # 年利率X%
        annual = float(m.group(1))
        if annual > threshold:
            exceeded.append((m.group(0), f"年利率{m.group(1)}%，超出保护上限"))
    if exceeded:
        return False, [e[0] for e in exceeded[:2]], \
            "利率约定超出司法保护上限（合同成立时一年期 LPR 四倍，年化约 %.0f%%）：" % threshold \
            + "；".join(e[1] for e in exceeded[:2])
    return True, [], ""


def h_contains_but_missing(text, sections, rule):
    """通用条件检查：出现某关键词但缺少应有要素。
    params.require: 触发关键词（任一出现才检查）
    params.forbid:  视为已具备的要素（regex，任一命中即通过）
    """
    params = rule.get("params") or {}
    seg = get_section(sections, rule.get("target", "whole"), text)
    require = params.get("require") or []
    forbid = params.get("forbid") or []
    hits = [p for p in require if re.search(p, seg)]
    if not hits:
        return True, [], ""          # 未触发
    for p in forbid:
        m = re.search(p, seg)
        if m:
            return True, [m.group(0)], ""
    return False, hits[:2], "出现\"" + hits[0] + "\"但未写明" + (rule.get("missing_label") or "相关要素")


def h_jurisdiction_card(text, sections, rule):
    """F2：按所选案由输出管辖规则提示卡（信息性，永远提示）。"""
    case = rule.get("_case_type", "general")
    cards = rule.get("cards") or {}
    card = cards.get(case) or cards.get("general", "")
    return False, [], card


HANDLERS: Dict[str, Callable[..., Tuple]] = {
    "money_amount": h_money_amount,
    "facts_amount": h_facts_amount,
    "interest_complete": h_interest_complete,
    "citation_format": h_citation_format,
    "plaintiff_id": h_plaintiff_id,
    "defendant_sufficient": h_defendant_sufficient,
    "rate_lpr4": h_rate_lpr4,
    "contains_but_missing": h_contains_but_missing,
    "jurisdiction_card": h_jurisdiction_card,
}


class RuleEngine:
    """加载规则库并执行检查。"""

    def __init__(self, rules_dir: Path):
        self.rules_dir = Path(rules_dir)
        self.common_rules: List[Dict[str, Any]] = []
        self.case_rules: Dict[str, Dict[str, Any]] = {}  # key -> {meta, rules}
        self._load()

    # ---------- 加载 ----------
    def _load(self):
        for f in sorted((self.rules_dir / "common").glob("*.yaml")):
            self.common_rules.extend(self._load_file(f))
        for f in sorted((self.rules_dir / "cases").glob("*.yaml")):
            data = self._load_file(f)
            meta = data.get("meta", {})
            key = meta.get("key") or f.stem
            self.case_rules[key] = {"meta": meta, "rules": data.get("rules", [])}

    @staticmethod
    def _load_file(path: Path) -> Any:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    # ---------- 对外接口 ----------
    @property
    def case_options(self) -> List[Dict[str, str]]:
        opts = [{"key": "general", "label": "通用（不限案由）",
                 "description": "仅执行通用检查项"}]
        for key, payload in self.case_rules.items():
            opts.append({"key": key, "label": payload["meta"].get("label", key),
                         "description": payload["meta"].get("description", "")})
        return opts

    def all_rules(self) -> List[Dict[str, Any]]:
        """溯源页用：全部检查项定义。"""
        out = [self._rule_meta(r, scope="通用") for r in self.common_rules]
        for key, payload in self.case_rules.items():
            for r in payload["rules"]:
                out.append(self._rule_meta(r, scope=payload["meta"].get("label", key)))
        return out

    @staticmethod
    def _rule_meta(r: Dict[str, Any], scope: str) -> Dict[str, Any]:
        return {
            "id": r.get("id"), "dimension": r.get("dimension"),
            "title": r.get("title"), "level": r.get("level"),
            "level_label": LEVEL_LABELS.get(r.get("level"), r.get("level")),
            "target": r.get("target"), "check": r.get("check"),
            "advice": r.get("advice"), "basis": r.get("basis"), "scope": scope,
        }

    def run(self, text: str, case_type: str = "general"):
        sections = split_sections(text)
        issues: List[Issue] = []
        applicable = [dict(r, _case_type=case_type) for r in self.common_rules]
        if case_type != "general":
            payload = self.case_rules.get(case_type)
            if payload is None:
                raise ValueError(f"未知案由：{case_type}")
            applicable.extend(payload["rules"])
            case_label = payload["meta"].get("label", case_type)
        else:
            case_label = "通用（不限案由）"

        for rule in applicable:
            passed, matched, detail, level_override = self._evaluate(rule, text, sections)
            if not passed:
                level = level_override or rule.get("level", "to_confirm")
                issues.append(Issue(
                    rule_id=rule.get("id", "?"),
                    dimension=rule.get("dimension", ""),
                    title=rule.get("title", ""),
                    level=level,
                    level_label=LEVEL_LABELS.get(level, level),
                    section=rule.get("target", "whole"),
                    message=detail or rule.get("advice", ""),
                    advice=rule.get("advice", ""),
                    basis=rule.get("basis", ""),
                    matched=matched,
                ))

        report = make_report(case_type, case_label, len(applicable), issues)
        report_dict = report.to_dict()
        report_dict["sections_found"] = {k: bool(v) for k, v in sections.items()}
        return report_dict

    # ---------- 单条规则执行 ----------
    def _evaluate(self, rule: Dict[str, Any], text: str,
                  sections: Dict[str, str]) -> Tuple[bool, list, str, Any]:
        check = rule.get("check", "")
        patterns = rule.get("patterns", []) or []
        seg = get_section(sections, rule.get("target", "whole"), text)

        if check.startswith("custom:"):
            name = check.split(":", 1)[1]
            handler = HANDLERS.get(name)
            if handler is None:
                raise ValueError(f"未注册的自定义检查：{name}")
            result = handler(text, sections, rule)
            if len(result) == 3:
                passed, matched, detail = result
                return passed, matched, detail, None
            passed, matched, detail, lvl = result
            return passed, matched, detail, lvl

        if check == "contains_any":
            hits = [p for p in patterns if p in seg]
            return (True, hits[:3], "", None) if hits else \
                (False, [], "未找到任一关键词：" + "、".join(patterns), None)
        if check == "contains_all":
            hits = [p for p in patterns if p in seg]
            missing = [p for p in patterns if p not in seg]
            return (True, hits[:3], "", None) if not missing else \
                (False, [], "缺少关键词：" + "、".join(missing), None)
        if check == "missing_all":
            hits = [p for p in patterns if p in seg]
            return (False, hits[:3], "出现应注意事项相关内容：" + "、".join(hits), None) \
                if hits else (True, [], "", None)
        if check == "regex_any":
            hits = []
            for p in patterns:
                m = re.search(p, seg)
                if m:
                    hits.append(m.group(0))
            return (True, hits[:3], "", None) if hits else \
                (False, [], "未匹配到任一格式", None)
        if check == "regex_all":
            hits, missing = [], []
            for p in patterns:
                m = re.search(p, seg)
                if m:
                    hits.append(m.group(0))
                else:
                    missing.append(p)
            return (True, hits[:3], "", None) if not missing else \
                (False, [], "缺少要素（规则：" + "、".join(missing) + "）", None)
        raise ValueError(f"未知检查类型：{check}")
