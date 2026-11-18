# -*- coding: utf-8 -*-
"""Rivulet 若 · 心情服务(独立模块,全局一份)

心情不属于 QQ,也不属于 B站 —— 它是「她」自己的状态,QQ / B站 / 工作区都读同一份。
QQ 里说话会改它,B站刷推荐/发说说会读它,面板随时能看。

    python mood_service.py          # 127.0.0.1:18099

HTTP:
    GET  /api/state                 当前心情(数值 + 一句话)
    GET  /api/brief                 拼给模型的「此刻的你」那段
    POST /api/observe   {text}      他说了一句话 → 心情跟着动
    POST /api/after_reply {text}    我说完一句 → 掉点力气
    POST /api/adjust    {mood?,energy?,cling?,hurt?,note?}  手动微调(面板/其它模块)
    GET  /api/health
"""
from __future__ import annotations

import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, HERE)
import mood as mood_mod  # noqa: E402

LOCK = threading.RLock()


def state() -> dict:
    with LOCK:
        s = mood_mod.state()
        s["brief"] = mood_mod.brief()
        s["words"] = {
            "mood": _word_mood(s["mood"]),
            "energy": _word_energy(s["energy"]),
            "cling": _word_cling(s["cling"], s["hurt"]),
            "hurt": _word_hurt(s["hurt"]),
        }
        return s


def _word_mood(m: float) -> str:
    if m > 0.55:
        return "心里亮堂堂的"
    if m > 0.25:
        return "心情挺好"
    if m > -0.1:
        return "心情平平的"
    if m > -0.4:
        return "有点闷"
    return "心里堵得慌"


def _word_energy(e: float) -> str:
    if e > 0.75:
        return "精神得很"
    if e > 0.5:
        return "还行,不困"
    if e > 0.3:
        return "有点乏"
    if e > 0.15:
        return "困得睁不开眼"
    return "困糊了"


def _word_cling(c: float, hurt: float) -> str:
    if hurt > 0.4:
        return "还疼着,收着点"
    if c > 0.75:
        return "特别黏人"
    if c > 0.5:
        return "想多待一会儿"
    return "收着点"


def _word_hurt(h: float) -> str:
    if h > 0.6:
        return "很委屈"
    if h > 0.35:
        return "有点委屈"
    if h > 0.1:
        return "有一点点不舒服"
    return "没事"


class Handler(BaseHTTPRequestHandler):
    server_version = "RivuletMood/1.0"

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
        if u.path == "/api/state":
            return self._send(200, {"ok": True, **state()})
        if u.path == "/api/brief":
            with LOCK:
                return self._send(200, {"ok": True, "brief": mood_mod.brief()})
        if u.path == "/api/health":
            return self._send(200, {"ok": True, "service": "rivulet-mood"})
        return self._send(404, {"ok": False, "error": "no such route"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        try:
            if u.path == "/api/observe":
                with LOCK:
                    mood_mod.observe(str(body.get("text") or ""))
                return self._send(200, {"ok": True, **state()})
            if u.path == "/api/after_reply":
                with LOCK:
                    mood_mod.after_reply(str(body.get("text") or ""))
                return self._send(200, {"ok": True, **state()})
            if u.path == "/api/adjust":
                with LOCK:
                    s = mood_mod._load()
                    for k, lo, hi in (("mood", -1.0, 1.0), ("energy", 0.0, 1.0),
                                      ("cling", 0.0, 1.0), ("hurt", 0.0, 1.0)):
                        if body.get(k) is not None:
                            s[k] = max(lo, min(hi, float(body[k])))
                    if body.get("note"):
                        mood_mod._note(s, str(body["note"])[:60])
                    mood_mod._save(s)
                return self._send(200, {"ok": True, **state()})
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)[:200]})
        return self._send(404, {"ok": False, "error": "no such route"})


def main():
    port = int(os.environ.get("RIVULET_MOOD_PORT") or 18099)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[mood] http://127.0.0.1:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
