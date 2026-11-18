# -*- coding: utf-8 -*-
"""Fatfish Core —— 独立进程、端口 8090、独立单元。

不引用 8080 账号服务(/opt/dhf)的任何东西。
"""
from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import db
import obs
import routes_obs
from routes_auth import router as auth_router
from routes_cmds import router as cmd_router
from routes_nodes import router as node_router, user_router as nodes_user_router

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.set_path(CONFIG["db_path"])
    db.init(CONFIG["db_path"])
    # 把 Rivulet 若 的本地功能服务(QQ/B站/人格/心情/一键部署)一起拉起来
    try:
        import rivulet_services
        n = rivulet_services.ensure()
        if n:
            print("[rivulet] services started: %d" % n, flush=True)
    except Exception as e:
        print("[rivulet] services failed: %r" % (e,), flush=True)
    print("[fatfish-core] up on :%s pid=%s" % (CONFIG["port"], os.getpid()), flush=True)
    yield
    print("[fatfish-core] down", flush=True)


app = FastAPI(title="Fatfish Core", version=CONFIG["version"], lifespan=lifespan)

# 可观测性:请求 ID / 单行 JSON 日志 / 指标 / 异常落库(obs.py)
obs.install(app, CONFIG)
# 可观测性接口:features.obs=false → 不挂(404);调试端点看 obs.debug_endpoints
routes_obs.mount(app, CONFIG)

app.include_router(auth_router)
app.include_router(node_router)
app.include_router(nodes_user_router)
app.include_router(cmd_router)

# 网页控制面板:纯静态单文件,不依赖 CDN(断网也能开)。挂在 /panel
STATIC_DIR = BASE / "static"
if STATIC_DIR.is_dir():
    app.mount("/panel", StaticFiles(directory=str(STATIC_DIR), html=True), name="panel")


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "service": CONFIG["service"],
        "version": CONFIG["version"],
        "port": CONFIG["port"],
        "pid": os.getpid(),
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "db": db.status(CONFIG["db_path"]),
    }


@app.get("/api/version")
def version() -> dict:
    return {"service": CONFIG["service"], "version": CONFIG["version"], "pid": os.getpid()}


@app.get("/")
def index() -> dict:
    return {
        "ok": True,
        "service": CONFIG["service"],
        "version": CONFIG["version"],
        "endpoints": [
            "POST /api/auth/login",
            "POST /api/auth/logout",
            "GET  /api/auth/me",
            "POST /api/auth/password",
            "GET  /api/auth/users  (admin)",
            "POST /api/auth/users  (admin)",
            "DELETE /api/auth/users/{id}  (admin)",
            "POST /api/node/register",
            "POST /api/node/poll     (X-Node-Token)",
            "POST /api/node/result   (X-Node-Token)",
            "GET  /api/nodes",
            "POST /api/nodes/{id}/reset-token",
            "DELETE /api/nodes/{id}",
            "POST /api/cmd",
            "GET  /api/cmd",
            "GET  /api/cmd/stats",
            "POST /api/cmd/{id}/cancel",
            "GET  /api/obs/metrics  (admin)",
            "GET  /api/obs/events   (admin)",
            "GET  /health",
        ],
    }


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # 原样的纯文本行保留(老习惯、grep 方便),只把异常文本过一遍脱敏:
    # 否则异常里带的 Bearer 令牌会原样进 core.log(验收 8)
    print("[fatfish-core][error] %s %s: %r" % (request.method, request.url.path, obs.redact(str(exc))), flush=True)
    # 异常追踪三层:日志 + obs_events(带 traceback)+ 响应体 request_id。
    # 整段 try/except:观测层自己坏了,也不能影响 500 响应本身。
    rid = ""
    try:
        state = request.scope.setdefault("state", {})
        rid = state.get("rid") or ""
        rid = obs.log_exc(
            {
                "rid": rid,
                "method": request.method,
                "path": request.url.path,
                "ip": request.client.host if request.client else "",
            },
            exc,
            dur_ms=state.get("dur_ms"),
        ) or rid
        state["obs_exc_logged"] = True
    except Exception:
        pass
    body = {"ok": False, "error": str(exc)}
    headers = {}
    if rid:
        body["request_id"] = rid
        headers["X-Request-Id"] = rid
    return JSONResponse(status_code=500, content=body, headers=headers)


if __name__ == "__main__":
    uvicorn.run("main:app", host=CONFIG["host"], port=CONFIG["port"], log_level="info")
