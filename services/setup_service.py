# -*- coding: utf-8 -*-
"""Status for first-run setup and Bilibili QR login."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

QQ_API = (os.environ.get("RIVULET_QQ") or "http://127.0.0.1:18096").rstrip("/")
BILI_API = (os.environ.get("RIVULET_BILI") or "http://127.0.0.1:18097").rstrip("/")
WS_API = (os.environ.get("RIVULET_WORKSPACE") or "http://127.0.0.1:18095").rstrip("/")

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
        account = _get(BILI_API + "/api/status", timeout=7)
        if account.get("logged_in"):
            step.update(state="done", detail="B 站账号已登录。")
            return step
        scan = _get(BILI_API + "/api/login/status")
        if scan.get("state") in ("gen", "qrcode", "scanned", "verifying"):
            step.update(state="waiting", detail=scan["hint"])
        elif scan.get("state") == "error":
            step.update(state="error", detail=scan["hint"])
        elif account.get("error"):
            step.update(state="error", detail=account["error"])
    except (OSError, ValueError):
        step.update(detail="B 站服务暂时连不上，请稍后刷新。")
    return step


def setup_status() -> dict:
    steps = [model_step(), qq_step(), bili_step()]
    done = sum(s["state"] == "done" for s in steps)
    return {"ok": True, "steps": steps, "done": done,
            "total": len(steps), "finished": done == len(steps)}


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
            try:
                return self._send(200, _get(BILI_API + "/api/login/status"))
            except (OSError, ValueError):
                return self._send(200, {"ok": False, "state": "error",
                                        "hint": "暂时连不上 B 站扫码服务，请稍后重试。"})
        if path == "/api/health":
            return self._send(200, {"ok": True, "service": "rivulet-setup"})
        return self._send(404, {"ok": False, "error": "no such route"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        size = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(size)) if size else {}
            if path == "/api/setup/bili/gen":
                return self._send(200, _post(BILI_API + "/api/login/gen", timeout=5))
            if path == "/api/setup/bili/verify":
                return self._send(200, _post(BILI_API + "/api/login/verify", timeout=5))
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
