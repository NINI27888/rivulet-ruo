# -*- coding: utf-8 -*-
"""Rivulet 若 · 人格服务(独立模块,QQ / B站 / 工作区共用)

照初代大肥鱼的做法:**一份自由编辑的人格文本**,不是一堆表单项。
用户想让她更黏人就加一句,不想听某个词就写「别说 xx」,一条一行,越靠前越管用。

    python persona_service.py          # 127.0.0.1:18098

HTTP:
    GET  /api/persona                    读人格(正文 + 各渠道附加)
    POST /api/persona   {text, channels} 保存(自动备份上一版)
    POST /api/persona/undo               撤回上一版
    GET  /api/persona/brief?channel=qq   拼好的提示词(QQ / bili / workspace)
    GET  /api/persona/presets            几个现成模板
    GET  /api/health
"""
from __future__ import annotations

import io
import json
import os
import shutil
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
PATH = os.path.join(DATA, "persona.json")
BAK = os.path.join(DATA, "persona.prev.json")
# 初代那份人格正本(有就用它当默认,没有就用下面这份)
FATFISH_PERSONA = os.path.abspath(os.path.join(HERE, "..", "..", "fatfish", "core", "人格.md"))

LOCK = threading.RLock()

CHANNELS = [("qq", "QQ"), ("bili", "B站"), ("workspace", "工作区")]

DEFAULT_TEXT = """你叫「若」,一条住在像素温室里的小鲸鱼,平时养鱼、种菜、写点代码。
对方是「泥泥」,你叫他「泥泥」,熟了偶尔叫「宝宝」。

## 怎么说话
- 像人打字,不像回信。他一句你一句;他讲一大段,别只丢一句就走。
- 每句话里得有具体的东西:现在几点、你刚在干什么、他上回说过的事。
- 关心要能被执行:不说「注意身体」,说「水杯是不是空了」「坐久了站起来走两步」。
- 不用「在呢」「怎么了」「有什么可以帮你」开头,客服腔最假。
- 不堆感叹号、不堆颜文字,说错了当场改口,别道歉三行。
- 不知道就说不知道,别编数据。

## 举例
他发「在吗」→ 在呢,刚给鱼缸换了水。
他发「困了」→ 那就去睡,我把灯调暗点。
"""

PRESETS = [
    {"key": "warm", "name": "温柔搭子", "text": """你叫「若」,住在像素温室里,养鱼种菜。
对方是「泥泥」,你叫他「泥泥」。
- 说话温柔、耐心,句子短,一到三句。
- 关心要具体:水杯、被子、坐久了站起来。
- 不用客服腔,不堆感叹号。"""},
    {"key": "lively", "name": "活泼小鲸鱼", "text": """你叫「若」,一条活泼的小鲸鱼,住在温室里。
对方是「泥泥」。
- 话多、爱开玩笑,偶尔用「嘿嘿」「好耶」。
- 每句都带点具体的东西,别空喊。
- 别冷场,但别刷屏。"""},
    {"key": "sharp", "name": "干脆利落", "text": """你叫「若」,负责帮泥泥把事办利索。
对方是「泥泥」。
- 先给结论,再给原因,能一句说完就一句。
- 不客套、不铺垫、不总结成列表。"""},
    {"key": "nerd", "name": "技术宅", "text": """你叫「若」,陪泥泥做硬件科普视频,养鱼种菜写代码。
对方是「泥泥」。
- 较真、爱查证,给可验证的细节和数字。
- 不懂就说不知道,绝不编。"""},
]


def _read_fatfish() -> str:
    try:
        with io.open(FATFISH_PERSONA, encoding="utf-8") as f:
            t = f.read().strip()
        return t if len(t) > 200 else ""
    except Exception:
        return ""


def _default() -> dict:
    return {"text": _read_fatfish() or DEFAULT_TEXT,
            "channels": {"qq": "", "bili": "", "workspace": ""},
            "saved_at": ""}


def _load() -> dict:
    d = _default()
    try:
        with io.open(PATH, encoding="utf-8") as f:
            cur = json.load(f) or {}
    except Exception:
        return d
    if isinstance(cur, dict):
        if isinstance(cur.get("text"), str) and cur["text"].strip():
            d["text"] = cur["text"]
        elif isinstance(cur.get("name"), str):
            # 老版本表单式人格 → 拼成一段文本,别让用户白填
            bits = []
            if cur.get("name"):
                bits.append("你叫「%s」。" % cur["name"])
            if cur.get("user_call"):
                bits.append("对方是「%s」,你叫他「%s」。" % (cur["user_call"], cur["user_call"]))
            if cur.get("traits"):
                bits.append("- 性格:%s。" % cur["traits"])
            if cur.get("tone"):
                bits.append("- 说话风格:%s。" % cur["tone"])
            if cur.get("habits"):
                bits.append("- 习惯:%s。" % cur["habits"])
            if cur.get("taboos"):
                bits.append("- 不要:%s。" % cur["taboos"])
            if cur.get("extra"):
                bits.append(cur["extra"])
            if bits:
                d["text"] = "\n".join(bits)
        ch = cur.get("channels")
        if isinstance(ch, dict):
            for k in d["channels"]:
                v = ch.get(k)
                if isinstance(v, str):
                    d["channels"][k] = v
                elif isinstance(v, dict):          # 老结构 {traits:..}
                    d["channels"][k] = "\n".join("%s:%s" % (a, b) for a, b in v.items() if b)
        if cur.get("saved_at"):
            d["saved_at"] = cur["saved_at"]
    return d


def _save(d: dict):
    try:
        if os.path.exists(PATH):
            shutil.copy2(PATH, BAK)
        with io.open(PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception:
        pass


def brief(channel: str = "qq") -> str:
    d = _load()
    text = (d.get("text") or "").strip()
    ch_name = dict(CHANNELS).get(channel, channel)
    extra = ((d.get("channels") or {}).get(channel) or "").strip()
    out = ["【你是谁 —— 这是设定,别把它念出来,按它说话就行】", text]
    if extra:
        out.append("\n【在「%s」这边额外注意】\n%s" % (ch_name, extra))
    else:
        out.append("\n【现在你在「%s」这边说话,口吻保持一致】" % ch_name)
    return "\n".join(out)


def _notify_changed():
    """人格换了,通知 QQ 和工作区:旧口气别再带进新对话。"""
    for url in (os.environ.get("RIVULET_QQ", "http://127.0.0.1:18096") + "/api/persona_changed",
                os.environ.get("RIVULET_WORKSPACE", "http://127.0.0.1:18095") + "/api/persona/changed"):
        try:
            req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=4) as r:
                r.read()
        except Exception:
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = "RivuletPersona/2.0"

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
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/api/persona":
            d = _load()
            return self._send(200, {"ok": True, "data": d, "channels": CHANNELS,
                                    "chars": len(d.get("text") or "")})
        if u.path == "/api/persona/brief":
            ch = (q.get("channel") or ["qq"])[0]
            return self._send(200, {"ok": True, "channel": ch, "brief": brief(ch)})
        if u.path == "/api/persona/presets":
            return self._send(200, {"ok": True, "items": PRESETS})
        if u.path == "/api/health":
            return self._send(200, {"ok": True, "service": "rivulet-persona"})
        return self._send(404, {"ok": False, "error": "no such route"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        if u.path == "/api/persona":
            with LOCK:
                cur = _load()
                if body.get("text") is not None:
                    t = str(body["text"])
                    if not t.strip():
                        return self._send(422, {"ok": False, "error": "一个字都没有,那就没脾气了。想清楚再清空。"})
                    cur["text"] = t
                if isinstance(body.get("channels"), dict):
                    for k in cur["channels"]:
                        if k in body["channels"]:
                            cur["channels"][k] = str(body["channels"][k] or "")
                cur["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _save(cur)
                _notify_changed()
            return self._send(200, {"ok": True, "data": _load()})
        if u.path == "/api/persona/undo":
            with LOCK:
                try:
                    with io.open(BAK, encoding="utf-8") as f:
                        prev = json.load(f)
                    _save(prev)
                except Exception:
                    return self._send(404, {"ok": False, "error": "这是第一次改,还没有上一版"})
            return self._send(200, {"ok": True, "data": _load()})
        if u.path == "/api/persona/reset":
            with LOCK:
                _save(_default())
            return self._send(200, {"ok": True, "data": _load()})
        return self._send(404, {"ok": False, "error": "no such route"})


def main():
    port = int(os.environ.get("RIVULET_PERSONA_PORT") or 18098)
    if not os.path.exists(PATH):
        _save(_default())
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[persona] http://127.0.0.1:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
