# -*- coding: utf-8 -*-
"""API 接口测试。"""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.main import app  # noqa: E402

client = TestClient(app)


def test_cases_endpoint():
    r = client.get("/api/cases")
    assert r.status_code == 200
    keys = {c["key"] for c in r.json()["cases"]}
    assert {"general", "jiamin", "maimai", "qinquan"} <= keys


def test_rules_endpoint():
    r = client.get("/api/rules")
    assert r.status_code == 200
    assert len(r.json()["rules"]) == 35


def test_check_endpoint():
    text = (BASE / "samples" / "sample_1_民间借贷.txt").read_text(encoding="utf-8")
    r = client.post("/api/check", json={"text": text, "case_type": "jiamin"})
    assert r.status_code == 200
    body = r.json()
    assert body["score"] == 85  # 示例一月息2分超 LPR 四倍 → D-J3 必须修改 -15
    d_j3 = [i for i in body["issues"] if i["rule_id"] == "D-J3"]
    assert d_j3 and d_j3[0]["level"] == "must_fix"
    assert body["case_label"] == "民间借贷纠纷"
    assert "sections_found" in body


def test_check_empty_text():
    r = client.post("/api/check", json={"text": "  ", "case_type": "general"})
    assert r.status_code == 400


def test_check_bad_case():
    r = client.post("/api/check", json={"text": "测试", "case_type": "wrong"})
    assert r.status_code == 400


def test_samples_endpoints():
    r = client.get("/api/samples")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["samples"]]
    assert len(names) == 2
    d = client.get(f"/api/samples/{names[0]}")
    assert d.status_code == 200 and d.json()["text"].strip()


def test_upload_txt():
    text = (BASE / "samples" / "sample_2_买卖合同.txt").read_bytes()
    r = client.post("/api/check-file",
                    files={"file": ("s.txt", text, "text/plain")},
                    data={"case_type": "maimai"})
    assert r.status_code == 200 and r.json()["score"] == 100


def test_homepage_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "起诉状自查工具" in r.text
