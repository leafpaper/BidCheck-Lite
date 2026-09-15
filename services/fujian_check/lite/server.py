"""标书检查 Lite：只有"检查"一个功能的单页小程序。

启动：python -m services.fujian_check.lite.server  （或双击仓库根目录的 检查标书.bat）
不需要数据库、不需要登录；文件与报告落在 ./lite_data/jobs/<id>/。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

FROZEN = getattr(sys, "frozen", False)                       # PyInstaller 打包后为 True
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if FROZEN:
    HERE = HERE / "services" / "fujian_check" / "lite"
    REPO = Path(sys.executable).resolve().parent              # exe 所在目录当作"根"
else:
    REPO = Path(__file__).resolve().parents[3]
DATA = Path(os.environ.get("FJ_LITE_DATA", REPO / "lite_data")).resolve()
JOBS = DATA / "jobs"
SETTINGS = DATA / "settings.json"
PORT = int(os.environ.get("FJ_LITE_PORT", "8765"))
PLACEHOLDER = re.compile(r"^\s*$|^<|your[-_]?key|xxx", re.I)
DEFAULT_LLM_BASE = "https://api.deepseek.com/v1"
DEFAULT_LLM_MODEL = "deepseek-flash"      # 2026 DeepSeek 开放接口的模型名：deepseek-flash / deepseek-v4-pro

app = FastAPI(title="标书检查 Lite", docs_url=None, redoc_url=None)
_live: dict[str, dict[str, Any]] = {}


# ---------- 环境与设置 ----------
def _load_env() -> None:
    """把根目录 .env 里的键写进 os.environ（已存在的不覆盖）。打包版通常没有 .env。"""
    f = REPO / ".env"
    if not f.exists():
        return
    for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _read_settings() -> dict[str, str]:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    except Exception:
        return {}


def _apply_settings() -> None:
    """页面里填的 key 优先于 .env：写进 os.environ 供引擎（rules/ocr.py 直接读环境变量）使用。"""
    s = _read_settings()
    if s.get("aliyun_key"):
        os.environ["ALIYUN_API_KEY"] = s["aliyun_key"]
    if s.get("llm_key"):
        os.environ["FJ_LLM_API_KEY"] = s["llm_key"]
    if s.get("llm_base"):
        os.environ["FJ_LLM_API_BASE"] = s["llm_base"]
    if s.get("llm_model"):
        os.environ["FJ_LLM_MODEL"] = s["llm_model"]


def _ocr_key() -> str:
    return os.environ.get("ALIYUN_API_KEY", "")


def _ocr_ready() -> bool:
    return not PLACEHOLDER.search(_ocr_key())


def _llm_conf() -> dict[str, str] | None:
    key = os.environ.get("FJ_LLM_API_KEY") or os.environ.get("BMP_LLM_API_KEY", "")
    if PLACEHOLDER.search(key):
        return None
    model = os.environ.get("FJ_LLM_MODEL") or os.environ.get("BMP_LLM_DEFAULT_MODEL") or DEFAULT_LLM_MODEL
    return {
        "key": key,
        "base": os.environ.get("FJ_LLM_API_BASE") or os.environ.get("BMP_LLM_API_BASE") or DEFAULT_LLM_BASE,
        "model": model.split("/")[-1],     # 平台 .env 里是 litellm 风格 "deepseek/deepseek-chat"，去掉厂商前缀
    }


def _mask(k: str) -> str:
    return "" if not k else ("••••" + k[-4:] if len(k) > 8 else "••••")


class SimpleLLM:
    """OpenAI 兼容端点上的最小 collect_json 实现（引擎只用这一个方法）。"""

    def __init__(self, conf: dict[str, str]):
        from openai import AsyncOpenAI

        self.model = conf["model"]
        self.client = AsyncOpenAI(api_key=conf["key"], base_url=conf["base"])

    async def collect_json(self, messages, temperature: float = 0.1, max_tokens: int = 600, max_repair_attempts: int = 1, **_):
        last: Any = {}
        for _ in range(max_repair_attempts + 1):
            resp = await self.client.chat.completions.create(model=self.model, messages=messages, temperature=temperature, max_tokens=max_tokens)
            txt = (resp.choices[0].message.content or "").strip()
            data = _parse_json(txt)
            if data is not None:
                return data
            last = {}
            messages = messages + [{"role": "assistant", "content": txt}, {"role": "user", "content": "只输出一个合法 JSON 对象，不要任何解释。"}]
        return last


def _parse_json(txt: str):
    for cand in (txt, re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.S)):
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except Exception:
            pass
    m = re.search(r"\{.*\}", txt, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


# ---------- 任务 ----------
def _meta_path(job: str) -> Path:
    return JOBS / job / "meta.json"


def _write_meta(job: str, meta: dict) -> None:
    _meta_path(job).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _run_job(job: str, tender: Path, bid: Path, use_ocr: bool, use_llm: bool) -> None:
    """在工作线程里跑引擎（PDF 解析是同步重活，不能占用事件循环）。任何异常都写进任务状态。"""
    st = _live[job]

    async def progress(p: float, msg: str):
        st["progress"], st["message"] = round(p, 2), msg

    try:
        from services.fujian_check.engine import run_check
        from services.fujian_check.report import to_markdown, to_platform_payload

        _apply_settings()
        llm = SimpleLLM(_llm_conf()) if use_llm and _llm_conf() else None
        report = asyncio.run(run_check(str(tender), str(bid), llm=llm, use_llm=bool(llm), use_ocr=use_ocr and _ocr_ready(),
                                       progress=progress, ocr_budget=20))
        payload = to_platform_payload(report)
        d = JOBS / job
        (d / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        (d / "report.md").write_text(to_markdown(report), encoding="utf-8")
        meta = json.loads(_meta_path(job).read_text(encoding="utf-8"))
        meta.update(status="done", risk_level=report.risk_level, summary=payload["summary"], profile=report.profile.key,
                    elapsed_s=report.stats.elapsed_s, finished=time.time())
        _write_meta(job, meta)
        st.update(status="done", progress=1.0, message="完成")
    except Exception as e:  # 任何异常都给前端一个可读原因
        meta = json.loads(_meta_path(job).read_text(encoding="utf-8"))
        meta.update(status="error", error=f"{type(e).__name__}: {e}"[:500])
        _write_meta(job, meta)
        st.update(status="error", error=meta["error"])


# ---------- 路由 ----------
@app.exception_handler(Exception)
async def _any_error(request, exc):
    """任何未捕获异常：把原因直接显示在页面上并写日志，而不是只给一句 Internal Server Error。"""
    import traceback

    tb = traceback.format_exc()
    print(f"[error] {request.url.path}\n{tb}")
    body = (f"标书检查 Lite 出错了（{request.url.path}）\n\n{type(exc).__name__}: {exc}\n\n{tb}\n\n"
            f"数据目录：{DATA}\n程序目录：{HERE}\n把这段文字或 lite_data/server.log 发给维护者即可定位。")
    return PlainTextResponse(body, status_code=500)


def _index_html() -> str:
    cands = [HERE / "index.html", Path(__file__).resolve().parent / "index.html"]
    if FROZEN:
        base = Path(getattr(sys, "_MEIPASS", REPO))
        cands += list(base.rglob("index.html"))[:5]
    for c in cands:
        if c.exists():
            return c.read_text(encoding="utf-8")
    raise FileNotFoundError(f"找不到页面文件 index.html，已尝试：{[str(c) for c in cands]}")


@app.get("/", response_class=HTMLResponse)
def index():
    return _index_html()


@app.get("/api/settings")
def settings():
    conf = _llm_conf()
    return {"ocr": _ocr_ready(), "llm": bool(conf), "llm_model": conf["model"] if conf else None, "data_dir": str(DATA),
            "aliyun_key": _mask(_ocr_key()) if _ocr_ready() else "",
            "llm_key": _mask(conf["key"]) if conf else "",
            "llm_base": (conf or {}).get("base") or os.environ.get("FJ_LLM_API_BASE") or DEFAULT_LLM_BASE,
            "llm_model_val": (conf or {}).get("model") or os.environ.get("FJ_LLM_MODEL") or DEFAULT_LLM_MODEL}


@app.post("/api/settings")
async def save_settings(body: dict):
    """保存页面里填的 key。以 •••• 开头的值表示"没改"，空字符串表示清空。"""
    s = _read_settings()
    for k in ("aliyun_key", "llm_key", "llm_base", "llm_model"):
        if k not in body:
            continue
        v = str(body[k] or "").strip()
        if "•" in v[:6] or v.startswith("•"):
            continue
        if v:
            s[k] = v
        else:
            s.pop(k, None)
            for env in {"aliyun_key": ["ALIYUN_API_KEY"], "llm_key": ["FJ_LLM_API_KEY", "BMP_LLM_API_KEY"],
                        "llm_base": ["FJ_LLM_API_BASE"], "llm_model": ["FJ_LLM_MODEL"]}[k]:
                os.environ.pop(env, None)
    DATA.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    _apply_settings()
    return settings()


@app.post("/api/settings/test")
async def test_settings(body: dict):
    """用最小请求验证 key 是否可用；返回可读的中文结论。"""
    kind = body.get("kind")
    from openai import AsyncOpenAI

    try:
        if kind == "ocr":
            if not _ocr_ready():
                return {"ok": False, "message": "还没有填写阿里云 key"}
            from services.fujian_check.rules.ocr import VISION_BASE_URL, VISION_MODEL

            c = AsyncOpenAI(api_key=_ocr_key(), base_url=VISION_BASE_URL)
            await c.chat.completions.create(model=VISION_MODEL, messages=[{"role": "user", "content": "回复 ok"}], max_tokens=3)
            return {"ok": True, "message": f"阿里云 {VISION_MODEL} 可用"}
        conf = _llm_conf()
        if not conf:
            return {"ok": False, "message": "还没有填写 LLM key"}
        c = AsyncOpenAI(api_key=conf["key"], base_url=conf["base"])
        await c.chat.completions.create(model=conf["model"], messages=[{"role": "user", "content": "回复 ok"}], max_tokens=3)
        return {"ok": True, "message": f"{conf['model']} @ {conf['base']} 可用"}
    except Exception as e:
        msg = str(e)
        models: list[str] = []
        m = re.search(r"supported API model names are ([^.]+?)(?:, but|\.)", msg)
        if m:
            models = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
        if "401" in msg or "invalid_api_key" in msg or "Incorrect API key" in msg:
            hint = "key 无效（401）"
        elif models:
            hint = f"模型名不对，该接口可用：{'、'.join(models)}（已替你填入第一个，请重新保存并测试）"
        elif "404" in msg or "model" in msg.lower() and "not" in msg.lower():
            hint = "模型名或接口地址不对（404）"
        elif "Connection" in type(e).__name__ or "connect" in msg.lower():
            hint = "连不上接口地址（网络/代理）"
        else:
            hint = type(e).__name__
        return {"ok": False, "message": f"{hint}：{msg[:160]}", "models": models}


@app.get("/api/settings/models")
async def list_models():
    """向 LLM 接口要模型清单（OpenAI 兼容 /models）。"""
    conf = _llm_conf()
    if not conf:
        return {"ok": False, "message": "先填写并保存 LLM key", "models": []}
    from openai import AsyncOpenAI

    try:
        c = AsyncOpenAI(api_key=conf["key"], base_url=conf["base"])
        ids = sorted(m.id for m in (await c.models.list()).data)
        return {"ok": True, "models": ids, "current": conf["model"]}
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {str(e)[:160]}", "models": []}


# ---------- 缓存 / 退出 ----------
def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


@app.get("/api/cache")
def cache_info():
    return {"cache_mb": round(_dir_size(DATA / "cache") / 1e6, 1), "jobs_mb": round(_dir_size(JOBS) / 1e6, 1),
            "jobs": len(list(JOBS.glob("*/meta.json"))) if JOBS.exists() else 0}


@app.post("/api/cache/clear")
def cache_clear(body: dict):
    """清理解析缓存；body.jobs=true 时连同全部检查记录（含上传的文件）一起删。运行中的任务不动。"""
    shutil.rmtree(DATA / "cache", ignore_errors=True)
    if body.get("jobs"):
        for d in list(JOBS.glob("*")):
            if d.is_dir() and _live.get(d.name, {}).get("status") != "running":
                shutil.rmtree(d, ignore_errors=True)
    return cache_info()


@app.post("/api/quit")
def quit_app():
    """页面上的"退出程序"：0.3 秒后整个进程退出（打包版没有黑窗口可关）。"""
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


@app.post("/api/run")
async def run(tender: UploadFile = File(...), bid: UploadFile = File(...), use_ocr: bool = Form(True), use_llm: bool = Form(False)):
    for f in (tender, bid):
        if not (f.filename or "").lower().endswith((".pdf", ".docx")):
            raise HTTPException(400, f"只支持 PDF/DOCX：{f.filename}")
    job = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = JOBS / job
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for tag, f in (("tender", tender), ("bid", bid)):
        p = d / f"{tag}{Path(f.filename or '').suffix.lower()}"
        with p.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        paths.append(p)
    _write_meta(job, {"id": job, "status": "running", "tender": tender.filename, "bid": bid.filename, "created": time.time(),
                      "use_ocr": use_ocr, "use_llm": use_llm})
    _live[job] = {"status": "running", "progress": 0.0, "message": "排队中"}
    threading.Thread(target=_run_job, args=(job, paths[0], paths[1], use_ocr, use_llm), daemon=True).start()
    return {"id": job}


@app.get("/api/jobs")
def jobs():
    out = []
    if JOBS.exists():
        for m in sorted(JOBS.glob("*/meta.json"), reverse=True)[:30]:
            try:
                out.append(json.loads(m.read_text(encoding="utf-8")))
            except Exception:
                continue
    return out


@app.get("/api/job/{job}")
def job_status(job: str):
    mp = _meta_path(job)
    if not mp.exists():
        raise HTTPException(404, "任务不存在")
    meta = json.loads(mp.read_text(encoding="utf-8"))
    live = _live.get(job, {})
    res = {**meta, "progress": live.get("progress", 1.0 if meta["status"] == "done" else 0.0), "message": live.get("message", "")}
    if meta["status"] == "running" and not live:
        res["status"] = "error"
        res["error"] = "服务重启，任务中断，请重新检查"
    if res["status"] == "done":
        rp = JOBS / job / "report.json"
        if rp.exists():
            res["report"] = json.loads(rp.read_text(encoding="utf-8"))
    return JSONResponse(res)


@app.get("/api/job/{job}/report.{ext}")
def job_file(job: str, ext: str):
    if ext not in ("md", "json"):
        raise HTTPException(404)
    p = JOBS / job / f"report.{ext}"
    if not p.exists():
        raise HTTPException(404, "报告不存在")
    return FileResponse(p, filename=f"检查报告-{job}.{ext}", media_type="text/markdown" if ext == "md" else "application/json")


@app.delete("/api/job/{job}")
def job_delete(job: str):
    d = JOBS / job
    if d.exists() and d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    _live.pop(job, None)
    return {"ok": True}


def main() -> None:
    import uvicorn

    _load_env()
    _apply_settings()
    os.environ.setdefault("FJ_CACHE_DIR", str(DATA / "cache"))
    JOBS.mkdir(parents=True, exist_ok=True)
    if FROZEN:
        # 打包版不带控制台：输出与报错写到日志文件 lite_data/server.log
        try:
            log = open(DATA / "server.log", "a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = log
            print(f"\n==== 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} ====")
        except Exception:
            pass
    port = PORT
    for cand in range(PORT, PORT + 10):
        url = f"http://127.0.0.1:{cand}/"
        if _already_running(url):
            print(f"标书检查 Lite 已在运行，直接打开 {url}")
            webbrowser.open(url)
            return
        if _port_free(cand):
            port = cand
            break
        print(f"端口 {cand} 被其他程序占用，换下一个")
    url = f"http://127.0.0.1:{port}/"
    print(f"标书检查 Lite → {url}   （关闭此窗口即退出）")
    print(f"数据目录：{DATA}")
    if not os.environ.get("FJ_LITE_NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except Exception:
        import traceback

        print(traceback.format_exc())
        raise


def _port_free(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _already_running(url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url + "api/settings", timeout=1.5) as r:
            return r.status == 200 and b"data_dir" in r.read()
    except Exception:
        return False


def _make_icon(path: Path) -> None:
    """生成程序图标（蓝底白勾）；打包时由 build.spec 调用。"""
    from PIL import Image, ImageDraw

    imgs = []
    for size in (256, 128, 64, 48, 32, 16):
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        r = size // 5
        d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=(37, 99, 235, 255))
        w = max(2, size // 9)
        pts = [(size * 0.24, size * 0.52), (size * 0.43, size * 0.71), (size * 0.77, size * 0.32)]
        d.line(pts, fill=(255, 255, 255, 255), width=w, joint="curve")
        imgs.append(im)
    imgs[0].save(path, format="ICO", sizes=[(i.width, i.height) for i in imgs], append_images=imgs[1:])


if __name__ == "__main__":
    main()
