"""起诉状结构分段器：把整篇文书切分为有法律意义的部分。

分段结果是一个 dict，key 为以下之一（找不到的段为空字符串）：
- title      标题（首个非空行）
- plaintiff  原告信息段
- defendant  被告信息段
- claims     诉讼请求段
- facts      事实与理由段
- closing    此致 / 具状人 / 日期等落款部分
"""
import re
from typing import Dict, List

# 段落起始标记（按行匹配，容忍全角冒号与多余空格）
RE_PLAINTIFF = re.compile(r"^\s*原告[（(：:]", re.M)
RE_DEFENDANT = re.compile(r"^\s*被告[（(：:一二三四五六]", re.M)
RE_CLAIMS = re.compile(r"^\s*(诉讼请求|请求事项|诉讼请求：|请求事项：)\s*$", re.M)
RE_CLAIMS_LOOSE = re.compile(r"^\s*(诉讼请求|请求事项)", re.M)
RE_FACTS = re.compile(r"^\s*(事实(与|及)理由)[:：]?\s*$", re.M)
RE_FACTS_LOOSE = re.compile(r"^\s*事实(与|及)理由", re.M)
RE_CLOSING = re.compile(r"^\s*此致\s*$|^\s*具状人|^\s*起诉人[:：]", re.M)
RE_COURT = re.compile(r"^\s*此致.{0,2}.*人民法院|^\s*.{2,15}人民法院\s*$", re.M)


def _first_match(text: str, pattern: re.Pattern) -> int:
    m = pattern.search(text)
    return m.start() if m else -1


def split_sections(text: str) -> Dict[str, str]:
    """把整篇起诉状文本切分为各部分；任一标记找不到时做宽松兜底。"""
    t = text.strip()
    sections: Dict[str, str] = {
        "title": "", "plaintiff": "", "defendant": "",
        "claims": "", "facts": "", "closing": "",
    }

    lines = t.splitlines()
    # title：首个非空行
    for ln in lines:
        if ln.strip():
            sections["title"] = ln.strip()
            break

    # 当事人段：原告…被告…（到诉讼请求前）
    p_start = _first_match(t, RE_PLAINTIFF)
    d_start = _first_match(t, RE_DEFENDANT)
    c_start = _first_match(t, RE_CLAIMS)
    if c_start < 0:
        c_start = _first_match(t, RE_CLAIMS_LOOSE)
    f_start = _first_match(t, RE_FACTS)
    if f_start < 0:
        f_start = _first_match(t, RE_FACTS_LOOSE)
    cl_start = _first_match(t, RE_CLOSING)

    # 锚点排序兜底：未找到的边界用文档长度
    n = len(t)
    bounds = {"p": p_start, "d": d_start, "c": c_start, "f": f_start, "cl": cl_start}

    if p_start >= 0:
        end = [x for x in (d_start, c_start, f_start, cl_start) if x > p_start]
        sections["plaintiff"] = t[p_start:min(end)] if end else ""
    if d_start >= 0:
        end = [x for x in (c_start, f_start, cl_start) if x > d_start]
        sections["defendant"] = t[d_start:min(end)] if end else ""
    if c_start >= 0:
        end = [x for x in (f_start, cl_start) if x > c_start]
        sections["claims"] = t[c_start:min(end)] if end else t[c_start:]
    if f_start >= 0:
        end = [x for x in (cl_start,) if x > f_start]
        sections["facts"] = t[f_start:min(end)] if end else t[f_start:]
    if cl_start >= 0:
        sections["closing"] = t[cl_start:]

    return sections


def get_section(sections: Dict[str, str], target: str, text: str) -> str:
    """按规则 target 取文本；whole=全文，未知 target 兜底全文。"""
    if target == "whole":
        return text
    return sections.get(target, "")
