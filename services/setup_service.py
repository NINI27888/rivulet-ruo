# -*- coding: utf-8 -*-
"""Status for first-run setup and Bilibili QR login."""
from __future__ import annotations

import base64
import importlib
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QQ_API = (os.environ.get("RIVULET_QQ") or "http://127.0.0.1:18096").rstrip("/")
BILI_API = (os.environ.get("RIVULET_BILI") or "http://127.0.0.1:18097").rstrip("/")
WS_API = (os.environ.get("RIVULET_WORKSPACE") or "http://127.0.0.1:18095").rstrip("/")

LOCK = threading.Lock()
BILI = {"state": "idle", "hint": "", "qr_b64": ""}
BILI_THREAD = None


def _get(url: str, timeout: float = 4) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _post(url: str, body: dict | None = None, timeout: float = 25) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body or {}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def model_step() -> dict:
    try:
        current = _get(WS_API + "/api/models/status")
        if current.get("enabled"):
            return {"key": "model", "name": "工作区模型", "state": "done",
                    "detail": str(current.get("model") or "聊天模型已启用")}
        detail = current.get("hint") or "选择服务商，粘贴 API 密钥。"
    except (OSError, ValueError):
        detail = "工作区暂时连不上，请稍后刷新。"
    return {"key": "model", "name": "工作区模型", "state": "todo", "detail": detail}


def qq_step() -> dict:
    step = {"key": "qq", "name": "QQ 自动回复", "state": "todo",
            "detail": "用手机 QQ 扫码，并选一个聊天模型。"}
    try:
        status = _get(QQ_API + "/api/status")
        on = bool((status.get("ws") or {}).get("connected"))
        if on and status.get("model_ready") and status.get("reply_enabled"):
            step.update(state="done", detail="QQ 已登录，自动回复已开启。")
        elif on:
            step.update(state="waiting", detail="QQ 已登录，接着设置自动回复。")
        elif (status.get("napcat") or {}).get("running"):
            step.update(state="waiting", detail="回到 QQ 页面扫码，随后在手机上确认。")
    except (OSError, ValueError):
        step.update(detail="QQ 服务暂时连不上，请稍后刷新。")
    return step


def bili_step() -> dict:
    step = {"key": "bili", "name": "B 站账号", "state": "todo",
            "detail": "用手机哔哩哔哩扫码即可登录。"}
    try:
        if _get(BILI_API + "/api/status").get("logged_in"):
            step.update(state="done", detail="B 站账号已登录。")
            return step
    except (OSError, ValueError):
        step.update(detail="B 站服务暂时连不上，请稍后刷新。")
    with LOCK:
        if BILI["state"] in ("gen", "qrcode", "scanned"):
            step.update(state="waiting", detail=BILI["hint"])
        elif BILI["state"] == "error":
            step.update(state="error", detail=BILI["hint"])
    return step


def setup_status() -> dict:
    steps = [model_step(), qq_step(), bili_step()]
    done = sum(s["state"] == "done" for s in steps)
    return {"ok": True, "steps": steps, "done": done,
            "total": len(steps), "finished": done == len(steps)}


def _bili_state(state: str, hint: str, qr: str = "") -> None:
    with LOCK:
        BILI.update(state=state, hint=hint, qr_b64=qr)


def _bili_worker() -> None:
    try:
        for directory in (ROOT / "fatfish" / "bili", ROOT.parent / "fatfish" / "bili"):
            if (directory / "qr_login.py").is_file():
                if str(directory) not in sys.path:
                    sys.path.insert(0, str(directory))
                break
        login = importlib.import_module("qr_login")
        key, png = login.create_code()
        qr = base64.b64encode(png).decode("ascii")
        _bili_state("qrcode", "打开手机哔哩哔哩 App，扫一扫。", qr)
        until = time.monotonic() + 180
        failed = 0
        while time.monotonic() < until:
            try:
                current, cookies = login.check_code(key)
                failed = 0
            except OSError:
                failed += 1
                if failed >= 3:
                    raise
                time.sleep(2)
                continue
            if current == "ok":
                login.save_login(cookies)
                _bili_state("ok", "手机已确认，正在读取你的 B 站账号…")
                return
            if current == "expired":
                _bili_state("error", "二维码已过期，请重新生成。")
                return
            if current == "scanned":
                _bili_state("scanned", "已扫码，请在手机上点「确认登录」。", qr)
            time.sleep(2)
        _bili_state("error", "等待超时，请重新生成二维码。")
    except (ImportError, OSError, RuntimeError, ValueError):
        _bili_state("error", "暂时无法完成 B 站扫码，请检查网络并重试。")


def bili_gen() -> dict:
    global BILI_THREAD
    with LOCK:
        if BILI_THREAD and BILI_THREAD.is_alive():
            return {"ok": True, "already": True}
        BILI.update(state="gen", hint="正在生成二维码…", qr_b64="")
        BILI_THREAD = threading.Thread(target=_bili_worker, daemon=True)
        BILI_THREAD.start()
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/setup/status":
            return self._send(200, setup_status())
        if path == "/api/setup/bili/status":
            with LOCK:
                snapshot = dict(BILI)
            return self._send(200, {"ok": True, **snapshot})
        if path == "/api/health":
            return self._send(200, {"ok": True, "service": "rivulet-setup"})
        return self._send(404, {"ok": False, "error": "no such route"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        size = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(size)) if size else {}
            if path == "/api/setup/bili/gen":
                return self._send(200, bili_gen())
            if path == "/api/setup/qq/start":
                return self._send(200, _post(QQ_API + "/api/napcat/start"))
            if path == "/api/setup/model":
                return self._send(200, _post(WS_API + "/api/models/configure", body))
            if path == "/api/setup/model/test":
                return self._send(200, _post(WS_API + "/api/models/test"))
        except (OSError, ValueError):
            return self._send(200, {"ok": False, "error": "本机服务暂时连不上，请稍后重试。"})
        return self._send(404, {"ok": False, "error": "no such route"})


def main():
    port = int(os.environ.get("RIVULET_SETUP_PORT") or 18100)
    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
