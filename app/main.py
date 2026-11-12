"""ComplaintCheck FastAPI 后端。

隐私承诺（PRD 六-隐私）：文书仅在内存中处理，不落盘、不上传第三方。
"""
from pathlib import Path
from io import BytesIO
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.engine import RuleEngine

BASE_DIR = Path(__file__).resolve().parent.parent
RULES_DIR = BASE_DIR / "rules"
SAMPLES_DIR = BASE_DIR / "samples"

engine = RuleEngine(RULES_DIR)

app = FastAPI(
    title="ComplaintCheck 起诉状自查 API",
    description="基于确定性规则引擎的民事起诉状立案前体检（仅供学习参考，不构成法律意见）",
    version="1.0.0",
)


class CheckRequest(BaseModel):
    text: str
    case_type: str = "general"


def _extract_text(raw: bytes, filename: str) -> str:
    """从上传文件提取纯文本（.txt 直接解码；.docx 用 python-docx）。"""
    name = (filename or "").lower()
    if name.endswith(".docx"):
        from docx import Document  # 延迟导入，规则引擎不依赖
        doc = Document(BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    # txt：utf-8 失败回退 gbk
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise HTTPException(status_code=400, detail="无法识别文件编码，请另存为 UTF-8 的 .txt 或 .docx")


@app.get("/api/cases")
def api_cases():
    """案由下拉选项。"""
    return {"cases": engine.case_options}


@app.get("/api/rules")
def api_rules():
    """检查项溯源：全部规则定义。"""
    return {"rules": engine.all_rules()}


@app.post("/api/check")
def api_check(req: CheckRequest):
    """核心检查接口：粘贴文本 + 案由 → 体检报告。"""
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="文书内容为空")
    try:
        report = engine.run(req.text, req.case_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content=report, headers={"Cache-Control": "no-store"})


@app.post("/api/check-file")
async def api_check_file(file: UploadFile = File(...), case_type: str = Form("general")):
    """上传 .txt / .docx 文件检查。文件仅在内存中处理。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    text = _extract_text(raw, file.filename or "")
    report = engine.run(text, case_type)
    return JSONResponse(content=report, headers={"Cache-Control": "no-store"})


@app.get("/api/samples")
def api_samples():
    """示例文书列表。"""
    files = sorted(SAMPLES_DIR.glob("*.txt")) if SAMPLES_DIR.exists() else []
    samples = []
    for f in files:
        head = f.read_text(encoding="utf-8").splitlines()
        title = next((ln.strip() for ln in head if ln.strip()), f.stem)
        samples.append({"name": f.stem, "title": title})
    return {"samples": samples}


@app.get("/api/samples/{name}")
def api_sample(name: str):
    """读取一份示例文书全文。"""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="非法文件名")
    f = SAMPLES_DIR / f"{name}.txt"
    if not f.exists():
        raise HTTPException(status_code=404, detail="示例不存在")
    return {"name": name, "text": f.read_text(encoding="utf-8")}


# 静态托管前端（放在最后，避免拦截 /api/*）
app.mount("/", StaticFiles(directory=BASE_DIR / "web", html=True), name="web")
