# -*- coding: utf-8 -*-
"""Rivulet 若 · 一键部署服务(18100)

给第一次打开面板、什么都不懂的人用:三步走完就能用。

  第 1 步 QQ 机器人:后台拉起协议端 → 面板显示二维码 → 手机 QQ 扫码 → 自动连上
  第 2 步 B站:生成 B站 登录二维码 → 手机 B站 扫码 → 自动写入 cookie
  第 3 步 工作区模型:选一个服务商(ChatGPT / DeepSeek / 本地 Ollama / 中转站)→ 填 Key → 测通

HTTP:
    GET  /api/setup/status                三步各自的状态
    POST /api/setup/qq/start             拉起 QQ 协议端(无窗口)
    GET  /api/setup/qq/qr                QQ 二维码(png base64)
    POST /api/setup/bili/gen             生成 B站 登录二维码并开始轮询
    GET  /api/setup/bili/status          B站 扫码状态
    GET  /api/setup/model/presets        模型服务商模板
    POST /api/setup/model                保存并启用模型(会顺手停用旧的)
    POST /api/setup/model/test           用当前启用的模型测一句
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

QQ_API = os.environ.get("RIVULET_QQ") or "http://127.0.0.1:18096"
BILI_API = os.environ.get("RIVULET_BILI") or "http://127.0.0.1:18097"
WS_API = os.environ.get("RIVULET_WORKSPACE") or "http://127.0.0.1:18095"
WS_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "项目-AI平台", "workspace"))
def _find_fatfish():
    """fatfish 目录自适应:包内是 services 的同级,开发环境在上一级。"""
    for rel in (("..", "fatfish"), ("..", "..", "fatfish")):
        c = os.path.abspath(os.path.join(HERE, *rel))
        if os.path.isdir(c):
            return c
    return os.path.abspath(os.path.join(HERE, "..", "fatfish"))
FATFISH_BILI = os.path.join(_find_fatfish(), "bili")

LOCK = threading.RLock()
BILI = {"state": "idle", "hint": "", "qr_b64": "", "at": ""}
BILI_THREAD = None

PRESETS = [
    {"key": "openai", "name": "ChatGPT(OpenAI)", "provider": "openai",
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
     "how": "去 platform.openai.com → API keys 建一把,复制 sk- 开头的"},
    {"key": "deepseek", "name": "DeepSeek", "provider": "deepseek",
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "how": "去 platform.deepseek.com → API keys 建一把"},
    {"key": "ollama", "name": "本地 Ollama(免费/不联网)", "provider": "ollama",
     "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b",
     "how": "本机先跑 ollama serve,再 ollama pull qwen2.5:7b;Key 随便填"},
    {"key": "relay", "name": "中转站 / 其它 OpenAI 兼容", "provider": "custom",
     "base_url": "https://你的地址/v1", "model": "对方列表里的模型名",
     "how": "用对方给的接口地址(带 /v1)和模型名,Key 用对方给的"},
    {"key": "opencode", "name": "OpenCode Zen", "provider": "custom",
     "base_url": "https://后台给的地址/v1", "model": "后台列表里的模型名",
     "how": "用 OpenCode 后台给的 API 地址和 Key;只有订阅没有 API 的话接不了"},
]


def _get(url: str, timeout: float = 8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, body: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(url, data=json.dumps(body or {}, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _patch(url: str, body: dict, timeout: float = 10):
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="PATCH")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- 三步状态
def qq_step() -> dict:
    st = {"key": "qq", "name": "QQ 机器人", "state": "todo",
          "detail": "还没开始", "qr": "", "can": "start"}
    try:
        s = _get(QQ_API + "/api/status")
        acc = s.get("account") or {}
        np = s.get("napcat") or {}
        if s.get("ws", {}).get("connected"):
            st.update(state="done", can="", detail="已登录:" + (acc.get("nickname") or "") +
                                                   ("(" + acc.get("uin") + ")" if acc.get("uin") else ""))
        elif np.get("state") == "qrcode":
            st.update(state="waiting", can="qr", detail="协议端已启动,用手机 QQ 扫下面的码",
                      qr=np.get("qr_b64") or "")
        elif np.get("running"):
            st.update(state="waiting", can="qr", detail=np.get("hint") or "协议端在跑,等二维码")
        else:
            st.update(detail="协议端没启动 —— 点「开始」")
    except Exception as e:
        st.update(detail="QQ 服务没起来:" + str(e)[:80])
    return st


def bili_step() -> dict:
    st = {"key": "bili", "name": "B站", "state": "todo", "detail": "还没开始",
          "qr": "", "can": "gen"}
    try:
        s = _get(BILI_API + "/api/status")
        if s.get("logged_in"):
            st.update(state="done", can="", detail="已登录:" + (s.get("uname") or ""))
            return st
    except Exception as e:
        st.update(detail="B站 服务没起来:" + str(e)[:80])
        return st
    with LOCK:
        b = dict(BILI)
    if b["state"] in ("qrcode", "scanned", "error"):
        st.update(state=("waiting" if b["state"] in ("qrcode", "scanned") else "error"),
                  detail=b["hint"], qr=b.get("qr_b64") or "", can="gen")
    return st


def model_step() -> dict:
    st = {"key": "model", "name": "工作区模型", "state": "todo", "detail": "还没接模型",
          "can": "config", "model": "", "provider": ""}
    try:
        s = _get(WS_API + "/api/models/status")
        if s.get("enabled"):
            st.update(state="done", can="config", model=s.get("model") or "",
                      provider=s.get("provider") or "",
                      detail="已启用:" + (s.get("model") or "") + " · " + (s.get("provider") or ""))
        else:
            st.update(detail="还没启用模型 —— 点「配置」选一个")
    except Exception as e:
        st.update(detail="工作区服务没起来:" + str(e)[:80])
    return st


def setup_status() -> dict:
    steps = [qq_step(), bili_step(), model_step()]
    done = sum(1 for s in steps if s["state"] == "done")
    return {"ok": True, "steps": steps, "done": done, "total": len(steps),
            "finished": done == len(steps)}


# ---------------------------------------------------------------- QQ
def qq_start() -> dict:
    try:
        r = _post(QQ_API + "/api/napcat/start", {}, timeout=25)
        return r if isinstance(r, dict) else {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


# ---------------------------------------------------------------- B站 扫码
def _bili_worker():
    global BILI
    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run([sys.executable, os.path.join(FATFISH_BILI, "qr_login.py"), "gen"],
                           cwd=FATFISH_BILI, capture_output=True, text=True,
                           timeout=90, env=env, creationflags=0x08000000)
        png = os.path.join(os.path.dirname(FATFISH_BILI), "data", "qr.png")
        if not os.path.exists(png):
            with LOCK:
                BILI.update(state="error", hint="二维码没生成:" + ((r.stdout or "") + (r.stderr or ""))[-160:])
            return
        b64 = base64.b64encode(open(png, "rb").read()).decode()
        with LOCK:
            BILI.update(state="qrcode", hint="用手机 B站 扫这个码", qr_b64=b64, at=time.strftime("%H:%M:%S"))
        # 轮询登录结果(最多 150 秒)
        for _ in range(75):
            time.sleep(2)
            rr = subprocess.run([sys.executable, os.path.join(FATFISH_BILI, "qr_login.py"), "poll"],
                                cwd=FATFISH_BILI, capture_output=True, text=True,
                                timeout=60, env=env, creationflags=0x08000000)
            out = (rr.stdout or "") + (rr.stderr or "")
            if rr.returncode == 0 and ("登录成功" in out or "cookie" in out.lower()):
                with LOCK:
                    BILI.update(state="ok", hint="B站 已登录", at=time.strftime("%H:%M:%S"))
                return
            if "二维码已过期" in out or "expired" in out.lower():
                with LOCK:
                    BILI.update(state="error", hint="二维码过期了,重新生成一次")
                return
            with LOCK:
                if BILI["state"] == "qrcode":
                    BILI.update(state="scanned", hint="扫到了,手机上点确认")
        with LOCK:
            if BILI["state"] != "ok":
                BILI.update(state="error", hint="等太久了,重新生成一次二维码")
    except Exception as e:
        with LOCK:
            BILI.update(state="error", hint=str(e)[:160])


def bili_gen() -> dict:
    global BILI_THREAD
    with LOCK:
        if BILI_THREAD and BILI_THREAD.is_alive():
            return {"ok": True, "already": True}
        BILI.update(state="gen", hint="正在生成二维码…", qr_b64="", at=time.strftime("%H:%M:%S"))
    BILI_THREAD = threading.Thread(target=_bili_worker, daemon=True)
    BILI_THREAD.start()
    return {"ok": True}


def bili_status() -> dict:
    with LOCK:
        return {"ok": True, **BILI}


# ---------------------------------------------------------------- 工作区模型
def model_presets() -> dict:
    return {"ok": True, "items": PRESETS}


def model_save(body: dict) -> dict:
    provider = str(body.get("provider") or "custom").strip()
    base = str(body.get("base_url") or "").strip()
    model = str(body.get("model") or "").strip()
    key = str(body.get("api_key") or "").strip()
    if not model:
        return {"ok": False, "error": "模型名要填(比如 gpt-4o-mini / deepseek-chat)"}
    if not key and provider != "ollama":
        return {"ok": False, "error": "API Key 还没填 —— 去服务商后台复制一把完整 Key"}
    try:
        old = (_get(WS_API + "/api/models") or {}).get("items") or []
        for m in old:
            if m.get("enabled"):
                try:
                    _patch(WS_API + "/api/models/" + urllib.parse.quote(m["id"]), {"enabled": False})
                except Exception:
                    pass
        item = _post(WS_API + "/api/models", {
            "name": "主模型", "provider": provider, "base_url": base,
            "model": model, "api_key": key, "enabled": True}, timeout=20)
        return {"ok": True, "item": item.get("item")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def model_test() -> dict:
    try:
        r = _post(WS_API + "/api/chat", {"text": "用一句话打个招呼,别超过 20 字"}, timeout=120)
        return {"ok": bool(r.get("ok")), "reply": r.get("reply") or "", "error": r.get("error") or ""}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "RivuletSetup/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code: int, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/setup/status":
            return self._send(200, setup_status())
        if u.path == "/api/setup/qq/qr":
            st = qq_step()
            return self._send(200, {"ok": True, "qr": st.get("qr") or "", "state": st["state"],
                                    "detail": st["detail"]})
        if u.path == "/api/setup/bili/status":
            return self._send(200, bili_status())
        if u.path == "/api/setup/model/presets":
            return self._send(200, model_presets())
        if u.path == "/api/health":
            return self._send(200, {"ok": True, "service": "rivulet-setup"})
        return self._send(404, {"ok": False, "error": "no such route"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        try:
            if u.path == "/api/setup/qq/start":
                return self._send(200, qq_start())
            if u.path == "/api/setup/bili/gen":
                return self._send(200, bili_gen())
            if u.path == "/api/setup/model":
                return self._send(200, model_save(body))
            if u.path == "/api/setup/model/test":
                return self._send(200, model_test())
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)[:200]})
        return self._send(404, {"ok": False, "error": "no such route"})


def main():
    port = int(os.environ.get("RIVULET_SETUP_PORT") or 18100)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[setup] http://127.0.0.1:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
