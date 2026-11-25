# -*- coding: utf-8 -*-
"""Rivulet 若 · QQ 机器人服务(DSH 接入 QQ)

定位(纠正版):
    主人在 QQ 里直接和 DSH 对话。QQ 是外部聊天入口,DSH 是真正提供对话能力的主体。
    本服务只做三件事:
      1. 协议接入:NapCat 走 OneBot v11,负责 QQ 收消息/发消息;
      2. 白名单网关:只有白名单里的 QQ 号 / 群能触发 DSH,非白名单直接拦掉并记审计;
      3. DSH 桥:白名单消息交给 DSH(人格 + 记忆 + 模型),把回复发回 QQ。
    面板只做管理台(状态/白名单/配置/审计/启停),不做聊天窗口。

跑法:
    set PYTHONPATH=D:\\qqbot\\libs
    python qq_service.py          # 127.0.0.1:18096

HTTP:
    GET  /api/status
    GET  /api/whitelist
    POST /api/whitelist/add      {kind:"user"|"group", id:"<你的QQ>", note:"主人"}
    POST /api/whitelist/remove   {kind, id}
    POST /api/whitelist/enabled  {on:true|false}
    GET  /api/audit?limit=100
    GET  /api/config
    POST /api/config             {ws_url, account, model, api_key, group_at_only, reply_enabled}
    POST /api/napcat/start | /api/napcat/stop | /api/reload
    POST /api/test_send          {chat:"private:<你的QQ>", text:"..."}
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import random
import re
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import napcat as nc_mod
import qzone
from workspace.provider_llm import ModelError, complete_message, model_settings, probe_model

MOOD_API = os.environ.get("RIVULET_MOOD") or "http://127.0.0.1:18099"
WORKSPACE_API = (os.environ.get("RIVULET_WORKSPACE") or "http://127.0.0.1:18095").rstrip("/")


def mood_call(path: str, body: dict | None = None, timeout: float = 6.0) -> dict:
    """心情是独立服务(18099):QQ / B站 / 工作区共用同一份。"""
    try:
        url = MOOD_API.rstrip("/") + path
        if body is None:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


def mood_state() -> dict:
    return mood_call("/api/state") or {}


def mood_brief() -> str:
    return (mood_call("/api/brief") or {}).get("brief") or ""


def mood_observe(text: str):
    mood_call("/api/observe", {"text": text})


def mood_after(text: str):
    mood_call("/api/after_reply", {"text": text})

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
CFG_PATH = os.path.join(DATA, "qq.json")
LEGACY_CFG = os.path.join(HERE, "qq.json")
WL_PATH = os.path.join(DATA, "qq_whitelist.json")
AUDIT_LOG = os.path.join(DATA, "qq_audit.jsonl")
CHAT_LOG = os.path.join(DATA, "qq_chat.jsonl")

DEFAULT_CFG = {
    "ws_url": "ws://127.0.0.1:3001",
    "ws_token": "",
    "account": "",
    "owner": "",
    "napcat_dir": os.environ.get("RIVULET_NAPCAT_DIR", "napcat"),
    "napcat_launcher": os.environ.get("RIVULET_NAPCAT_LAUNCHER", "napcat/launcher.bat"),
    "napcat_headless_dir": os.environ.get("RIVULET_NAPCAT_HEADLESS", "fatfish/NapCat"),
    "memory_api": "http://127.0.0.1:18091",
    "reply_enabled": True,
    "use_workspace_model": False,
    "group_at_only": True,
    "reply_cooldown_sec": 1.5,
    "reply_max_per_min": 8,
    "history_turns": 12,
    "persona_file": "persona.txt",
    "dsh": {
        "api_base": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-flash",
        "max_tokens": 220,
        "temperature": 0.8,
        "timeout": 40,
    },
}
DEFAULT_WL = {"enabled": True, "users": [], "groups": []}


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _load_json(path: str, default: dict) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            base = json.loads(json.dumps(default))
            base.update(d)
            return base
    except Exception:
        pass
    return json.loads(json.dumps(default))


def _save_json(path: str, obj) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


CFG = _load_json(CFG_PATH if os.path.isfile(CFG_PATH) else LEGACY_CFG, DEFAULT_CFG)
CFG_LOCK = threading.RLock()
WL = _load_json(WL_PATH, DEFAULT_WL)


def save_cfg(changes: dict | None = None):
    with CFG_LOCK:
        saved = {**CFG, **(changes or {})}
        candidate = CFG_PATH + ".tmp"
        with open(candidate, "w", encoding="utf-8") as f:
            json.dump(saved, f, ensure_ascii=False, indent=2)
        os.replace(candidate, CFG_PATH)
        if changes:
            CFG.update(changes)


def workspace_model_status() -> dict:
    try:
        with urllib.request.urlopen(WORKSPACE_API + "/api/models/status", timeout=2) as response:
            return json.load(response) or {}
    except (OSError, ValueError):
        return {}


def qq_model_input() -> dict:
    dsh = CFG.get("dsh") or {}
    return {**dsh, "provider": "custom", "base_url": dsh.get("api_base")}


def qq_model_status() -> dict:
    if CFG.get("use_workspace_model"):
        model = workspace_model_status()
        return {"model_source": "workspace", "model": model.get("model") or "",
                "has_key": bool(model.get("has_key")), "model_ready": bool(model.get("enabled"))}
    current = qq_model_input()
    try:
        model_settings(current)
        ready = True
    except ModelError:
        ready = False
    return {"model_source": "custom", "model": (current.get("model") or "") if ready else "",
            "has_key": bool(current.get("api_key")), "model_ready": ready}


def configure_qq_model(body: dict) -> dict:
    try:
        existing = qq_model_input()
        # QQ only stores the resolved address. A key can be reused on that same address.
        existing["provider"] = body.get("provider") or "custom"
        model = model_settings(body, existing)
        probe_model(model)
        with CFG_LOCK:
            dsh = {**(CFG.get("dsh") or {}), "api_base": model["base_url"],
                   "model": model["model"], "api_key": model["api_key"]}
            save_cfg({"dsh": dsh, "use_workspace_model": False})
        return {"ok": True, "model": model["model"]}
    except ModelError as e:
        return {"ok": False, "error": str(e)}
    except OSError:
        return {"ok": False, "error": "保存失败。请检查程序所在文件夹是否可以写入。"}


def use_workspace_model() -> dict:
    if not workspace_model_status().get("enabled"):
        return {"ok": False, "error": "先在工作区测试并启用一个聊天模型。"}
    save_cfg({"use_workspace_model": True})
    return {"ok": True}


def qq_model_reply(messages: list[dict], max_tokens: int, temperature: float,
                   timeout: float = 40) -> str:
    if not CFG.get("use_workspace_model"):
        return complete_message(model_settings(qq_model_input()), messages,
                                max_tokens=max_tokens, temperature=temperature, timeout=timeout)["content"]
    req = urllib.request.Request(
        WORKSPACE_API + "/api/models/respond",
        data=json.dumps({"messages": messages, "max_tokens": max_tokens,
                         "temperature": temperature}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.load(response)
    except (OSError, ValueError):
        raise ModelError("暂时联系不上工作区模型，请重开若后再试。") from None
    if not result.get("ok"):
        raise ModelError(result.get("error") or "工作区模型没有返回回答。")
    return result["text"]


def save_wl():
    _save_json(WL_PATH, WL)


class Audit:
    def __init__(self, limit: int = 500):
        self.lock = threading.Lock()
        self.ring: list[dict] = []
        self.limit = limit
        self.allowed = 0
        self.blocked = 0
        self.replied = 0
        self.last_at = ""

    def add(self, item: dict):
        with self.lock:
            self.ring.append(item)
            if len(self.ring) > self.limit:
                del self.ring[: len(self.ring) - self.limit]
            self.last_at = item.get("time") or now()
            d = item.get("decision")
            if d == "allow":
                self.allowed += 1
            elif d == "block":
                self.blocked += 1
        try:
            with open(AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def recent(self, limit: int = 100) -> list[dict]:
        with self.lock:
            return list(self.ring[-limit:])


AUDIT = Audit()


def _preload_audit(limit: int = 200):
    try:
        if not os.path.exists(AUDIT_LOG):
            return
        lines = io.open(AUDIT_LOG, encoding="utf-8", errors="replace").read().split("\n")[-limit:]
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                item = json.loads(ln)
            except Exception:
                continue
            with AUDIT.lock:
                AUDIT.ring.append(item)
                d = item.get("decision")
                if d == "allow":
                    AUDIT.allowed += 1
                elif d == "block":
                    AUDIT.blocked += 1
                elif d == "reply":
                    AUDIT.replied += 1
                AUDIT.last_at = item.get("time") or AUDIT.last_at
    except Exception:
        pass


class ChatLog:
    def __init__(self):
        self.lock = threading.Lock()
        self.hist: dict[str, list[dict]] = {}

    def push(self, chat: str, role: str, text: str):
        with self.lock:
            h = self.hist.setdefault(chat, [])
            h.append({"role": role, "content": text, "at": time.time()})
            keep = max(2, int(CFG.get("history_turns", 12))) * 2
            if len(h) > keep:
                del h[: len(h) - keep]
        try:
            with open(CHAT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": now(), "chat": chat, "role": role, "text": text},
                                   ensure_ascii=False) + "\n")
        except Exception:
            pass

    def get(self, chat: str) -> list[dict]:
        with self.lock:
            return list(self.hist.get(chat, []))


CHAT = ChatLog()


def wl_check(chat: str, sender: str, is_group: bool, at_me: bool) -> tuple[bool, str]:
    """权限判定:消息链路第一道门,不放行就不进 DSH。"""
    if not WL.get("enabled"):
        return False, "白名单未启用(全部拦截)"
    if is_group:
        gid = chat.split(":", 1)[1] if ":" in chat else ""
        g = next((x for x in WL.get("groups", []) if str(x.get("id")) == str(gid)), None)
        if not g:
            return False, f"群 {gid} 不在白名单"
        if CFG.get("group_at_only") and not at_me:
            return False, "群里只回 @ 我的消息"
        return True, "群白名单放行"
    u = next((x for x in WL.get("users", []) if str(x.get("id")) == str(sender)), None)
    if not u:
        return False, f"QQ {sender} 不在白名单"
    return True, "用户白名单放行"


PERSONA_DEFAULT = (
    "你是 DSH(DeepSeek Harness),住在 Rivulet 若 这个温室里的 AI。"
    "说话短、自然、有点温柔,像跟主人聊天,不要客服腔,不要长篇大论,一般 1~3 句。"
)


PERSONA_API = os.environ.get("RIVULET_PERSONA") or "http://127.0.0.1:18098"


def persona() -> str:
    """人格从独立的人格服务取(QQ 渠道);取不到再退回本地 persona.txt。"""
    try:
        url = PERSONA_API.rstrip("/") + "/api/persona/brief?channel=qq"
        with urllib.request.urlopen(url, timeout=4) as r:
            b = (json.loads(r.read().decode("utf-8")) or {}).get("brief") or ""
        if b:
            return b[:1800]
    except Exception:
        pass
    p = os.path.join(HERE, CFG.get("persona_file") or "persona.txt")
    try:
        with open(p, encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            return t[:1800]
    except Exception:
        pass
    return PERSONA_DEFAULT


def memory_hints(text: str, limit: int = 5) -> list[str]:
    try:
        q = urllib.parse.quote((text or "")[:40])
        url = CFG["memory_api"].rstrip("/") + f"/api/memory/search?limit={limit}&q={q}"
        with urllib.request.urlopen(url, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [f"- {it.get('content', '')}" for it in (data.get("items") or [])][:limit]
    except Exception:
        return []


def write_memory(text: str, chat: str, sender: str):
    try:
        body = json.dumps({
            "content": text[:400], "source": "qq", "kind": "fact", "occurred_at": now(),
            "context": {"chat": chat, "sender": sender, "via": "rivulet-qq-dsh"},
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(CFG["memory_api"].rstrip("/") + "/api/memory",
                                     data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=4).read()
    except Exception:
        pass


PERSONA_EPOCH = 0.0


def dsh_reply(chat: str, sender: str, text: str) -> tuple[str, str]:
    """返回 (回复, 错误)。真模型;连不上如实报错,不编。"""
    dsh = CFG.get("dsh") or {}
    hints = memory_hints(text)
    mb = mood_brief()
    note = "\n\n【注意】如果下面的对话历史里有跟你现在设定不一致的口气,以现在设定为准,别沿用旧口气。"
    msgs = [{"role": "system", "content": persona() + ("\n\n" + mb if mb else "") + note}]
    if hints:
        msgs.append({"role": "system", "content": "可参考这些记忆(不一定都用):\n" + "\n".join(hints)})
    # 换过人格之后,旧历史里那种说话方式不要再带进来
    turns = int(CFG.get("history_turns", 12))
    hist = [x for x in CHAT.get(chat)[-turns * 2:] if float(x.get("at") or 0) > PERSONA_EPOCH]
    for h in hist[-turns:]:
        msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": text[:800]})
    try:
        out = qq_model_reply(msgs, int(dsh.get("max_tokens") or 220),
                             float(dsh.get("temperature") or 0.8), float(dsh.get("timeout") or 40))
        return out[:600], ""
    except ModelError as e:
        return "", str(e)


def plain_text(message) -> str:
    if isinstance(message, str):
        s = message
    elif isinstance(message, list):
        parts = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            t = seg.get("type")
            d = seg.get("data") or {}
            if t == "text":
                parts.append(d.get("text", ""))
            elif t == "image":
                parts.append("[图片]")
            elif t == "face":
                parts.append("[表情]")
            elif t == "at":
                parts.append("@" + str(d.get("qq", "")))
        s = "".join(parts)
    else:
        s = str(message or "")
    return re.sub(r"\s+", " ", s).strip()


class OneBot:
    def __init__(self):
        self.ws = None
        self.connected = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self._echo = 0
        self._pending: dict[str, asyncio.Future] = {}
        self.error = ""
        self.account: dict = {}
        self.groups: list[dict] = []
        self.friends: list[dict] = []
        self._last_reply: dict[str, float] = {}
        self._reply_times: list[float] = []
        self.last_send_error = ""

    async def action(self, name: str, params: dict | None = None, timeout: float = 10.0):
        if not self.ws:
            raise RuntimeError("还没连上协议端")
        self._echo += 1
        echo = f"r{self._echo}"
        fut = self.loop.create_future()
        self._pending[echo] = fut
        await self.ws.send(json.dumps({"action": name, "params": params or {}, "echo": echo}))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(echo, None)

    def call(self, name: str, params: dict | None = None, timeout: float = 10.0):
        if not self.loop:
            raise RuntimeError("事件循环还没起来")
        return asyncio.run_coroutine_threadsafe(self.action(name, params, timeout), self.loop).result(timeout + 2)

    async def run(self):
        import websockets
        while True:
            try:
                headers = {}
                if CFG.get("ws_token"):
                    headers["Authorization"] = "Bearer " + CFG["ws_token"]
                async with websockets.connect(CFG["ws_url"], additional_headers=headers,
                                              ping_interval=20, max_size=8 * 1024 * 1024) as ws:
                    self.ws = ws
                    self.connected = True
                    self.error = ""
                    print(f"[qq] 连上协议端 {CFG['ws_url']}", flush=True)
                    reader = asyncio.create_task(self._reader(ws))
                    await self._hello()
                    await reader
            except Exception as e:
                self.connected = False
                self.ws = None
                self.error = str(e)[:200]
                print(f"[qq] 断开:{e};3 秒后重连", flush=True)
                await asyncio.sleep(3)

    async def _reader(self, ws):
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if "echo" in msg and msg["echo"] in self._pending:
                fut = self._pending.get(msg["echo"])
                if fut and not fut.done():
                    fut.set_result(msg)
                continue
            if msg.get("post_type") == "message":
                asyncio.create_task(self.on_message(msg))

    async def _hello(self):
        try:
            r = await self.action("get_login_info", {})
            d = (r or {}).get("data") or {}
            self.account = {"uin": str(d.get("user_id", "")), "nickname": d.get("nickname", "")}
        except Exception:
            pass
        try:
            r = await self.action("get_group_list", {})
            self.groups = (r or {}).get("data") or []
        except Exception:
            pass
        try:
            r = await self.action("get_friend_list", {})
            self.friends = (r or {}).get("data") or []
        except Exception:
            pass

    async def on_message(self, msg: dict):
        mt = msg.get("message_type")
        sender = msg.get("sender") or {}
        uid = str(msg.get("user_id") or "")
        gid = str(msg.get("group_id") or "")
        text = plain_text(msg.get("message"))
        chat = ("group:" + gid) if mt == "group" else ("private:" + uid)
        name = sender.get("card") or sender.get("nickname") or uid
        at_me = False
        if mt == "group":
            self_id = str(self.account.get("uin") or "")
            raw = json.dumps(msg.get("message") or [], ensure_ascii=False)
            at_me = bool(self_id and (f'"qq": "{self_id}"' in raw or f'"qq":"{self_id}"' in raw))
            if not at_me and ("若" in text or "DSH" in text.upper()):
                at_me = True
        if not text:
            return
        ok, reason = wl_check(chat, uid, mt == "group", at_me)
        AUDIT.add({"time": now(), "chat": chat, "sender": uid, "sender_name": name,
                   "text": text[:200], "decision": "allow" if ok else "block", "reason": reason})
        if not ok:
            print(f"[qq] 拦截 {chat} {uid}:{reason}", flush=True)
            return
        write_memory(f"{name}:{text}", chat=chat, sender=name)
        mood_observe(text)
        if not CFG.get("reply_enabled"):
            return
        t = time.time()
        if t - self._last_reply.get(chat, 0) < float(CFG.get("reply_cooldown_sec", 1.5)):
            return
        self._reply_times = [x for x in self._reply_times if t - x < 60]
        if len(self._reply_times) >= int(CFG.get("reply_max_per_min", 8)):
            return
        self._last_reply[chat] = t
        self._reply_times.append(t)
        CHAT.push(chat, "user", text)
        loop = asyncio.get_running_loop()
        reply, err = await loop.run_in_executor(None, lambda: dsh_reply(chat, uid, text))
        if err:
            print(f"[qq] {err}", flush=True)
            AUDIT.add({"time": now(), "chat": chat, "sender": "dsh", "sender_name": "DSH",
                       "text": err, "decision": "error", "reason": err})
            return
        if not reply:
            return
        CHAT.push(chat, "assistant", reply)
        mood_after(reply)
        ok, err = await self.send(mt, gid or uid, reply)
        if ok:
            AUDIT.replied += 1
            AUDIT.add({"time": now(), "chat": chat, "sender": "dsh", "sender_name": "DSH",
                       "text": reply[:300], "decision": "reply", "reason": "已发送"})
        else:
            AUDIT.add({"time": now(), "chat": chat, "sender": "dsh", "sender_name": "DSH",
                       "text": reply[:160], "decision": "error", "reason": err})

    async def send(self, kind: str, target: str, text: str):
        action = "send_group_msg" if kind == "group" else "send_private_msg"
        key = "group_id" if kind == "group" else "user_id"
        try:
            r = await self.action(action, {key: int(target), "message": text})
        except Exception as e:
            self.last_send_error = "发送异常:%s" % str(e)[:160]
            return False, self.last_send_error
        ret = (r or {}).get("retcode")
        if ret not in (0, None):
            raw = json.dumps(r, ensure_ascii=False)[:220]
            reason = "发送被拒 retcode=%s" % ret
            if "Ban state" in raw or "-10203" in raw:
                reason = "这个 QQ 号还在封禁/限制状态,收得到但发不出去(Ban state)"
            self.last_send_error = reason
            return False, reason
        self.last_send_error = ""
        return True, ""


OB = OneBot()


def port_open(port: int = 3001) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def napcat_running() -> bool:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process | Where-Object { $_.ProcessName -match 'NapCat' } | Measure-Object).Count"],
            capture_output=True, text=True, timeout=8)
        return (out.stdout or "0").strip() not in ("", "0")
    except Exception:
        return False


NC = None


def _nc():
    """无头 NapCat 管理器:node.exe index.js,不弹窗、不要管理员。"""
    global NC
    d = (CFG.get("napcat_headless_dir") or "").strip()
    # 相对路径按「包根」(services 的上一级)解析,不依赖进程当前目录。
    # 否则以 services\ 为 cwd 启动时,"fatfish/NapCat" 会被判成不存在。
    if d and not os.path.isabs(d):
        _root = os.path.dirname(HERE)
        _cand = os.path.join(_root, d)
        if os.path.isdir(_cand):
            d = _cand
    # 同理兜底:配置为空时直接找包内的 fatfish\NapCat
    if not d:
        _cand = os.path.join(os.path.dirname(HERE), "fatfish", "NapCat")
        if os.path.isdir(_cand):
            d = _cand
    if NC is None:
        NC = nc_mod.NapCat(d)
    else:
        NC.d = d
    return NC


def _auto_claim_owner(nc) -> None:
    """扫码登录成功后,把登录的这个号自动设成主人并写进白名单。
    不然白名单一直是空的(enabled=True),任何人发消息都会被拦掉,
    表现就是「扫上码了、机器人一条都不回」。"""
    try:
        uin = ""
        try:
            uin = (nc_mod.uin_from_config(nc.d) or "").strip()
        except Exception:
            uin = ""
        if not uin:
            uin = str(CFG.get("account") or "").strip()
        if not uin:
            return
        changed = False
        if str(CFG.get("owner") or "").strip() != uin:
            CFG["owner"] = uin
            changed = True
        users = WL.get("users") or []
        if not any(str(x.get("id")) == uin for x in users):
            users.append({"id": uin, "note": "主人", "added_at": now()})
            WL["users"] = users
            changed = True
        if not WL.get("enabled"):
            WL["enabled"] = True
            changed = True
        if changed:
            save_cfg()
            save_wl()
    except Exception:
        pass


def napcat_state() -> dict:
    try:
        nc = _nc()
        st, hint = nc.state()
        if st == "ok":
            _auto_claim_owner(nc)
        qr = nc.qr()
        b64 = ""
        if os.path.exists(qr):
            try:
                import base64
                data = open(qr, "rb").read()
                if 0 < len(data) < 500_000:
                    b64 = base64.b64encode(data).decode()
            except Exception:
                b64 = ""
        return {"running": nc.running(), "state": st, "hint": hint,
                "qr": bool(b64), "qr_b64": b64, "dir": nc.d}
    except Exception as e:
        return {"running": False, "state": "error", "hint": str(e)[:160],
                "qr": False, "qr_b64": "", "dir": ""}


def start_napcat() -> dict:
    nc = _nc()
    if not nc_mod.is_headless(nc.d):
        return {"ok": False,
                "error": ("QQ 需要 NapCat 协议端(第三方组件,体积大、需单独下载,") 
                         + ("不在本安装包内)。下载 NapCat 后解压到程序目录的 "
                            "fatfish\\NapCat\\ 下(该目录内应有 node.exe 和 index.js)," 
                            "再点一次「启动协议端」。当前查找位置:" + (nc.d or "(未配置)"))}
    if nc.running():
        return {"ok": True, "already": True, "hint": "协议端已经在跑"}
    try:
        nc.start(uin=str(CFG.get("account") or ""))
        return {"ok": True, "hint": "协议端已在后台启动(无窗口、无 UAC);等二维码出来扫一下就行"}
    except Exception as e:
        return {"ok": False, "error": str(e)}



def restart_napcat() -> dict:
    """重新扫码:停掉协议端、删掉旧二维码、再起一次。
    用于「登录失败 / 账号存在安全风险 / 二维码过期」之后重新来一遍。
    注意:QQ 判定安全风险时,需要在手机 QQ 上完成验证,这里只能让它重新出码。"""
    nc = _nc()
    try:
        nc.stop()
    except Exception:
        pass
    # 删旧二维码,否则前端还是显示上一张
    try:
        qr = os.path.join(nc.d, "napcat", "cache", "qrcode.png")
        if os.path.exists(qr):
            os.remove(qr)
    except Exception:
        pass
    time.sleep(1)
    return start_napcat()


def test_model() -> dict:
    status = qq_model_status()
    if not status["model_ready"]:
        return {"ok": False, "error": "请先为 QQ 选择一个聊天模型。"}
    try:
        text = qq_model_reply([{"role": "user", "content": "请只回复 OK。"}], 128, 0.7, 18)
        return {"ok": True, "model": status["model"], "reply": text[:60]}
    except ModelError as e:
        return {"ok": False, "error": str(e)}

def stop_napcat() -> dict:
    try:
        _nc().stop()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}



# ================================================================ 心情 / 主动消息 / 说说
IDLE_STATE = os.path.join(DATA, "idle_state.json")
QZONE_STATE = os.path.join(DATA, "qzone_state.json")


def _st_load(path: str) -> dict:
    try:
        return json.loads(io.open(path, encoding="utf-8").read() or "{}")
    except Exception:
        return {}


def _st_save(path: str, d: dict):
    try:
        io.open(path, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=1))
    except Exception:
        pass


def idle_cfg() -> dict:
    c = CFG.get("idle") or {}
    return {
        "enabled": bool(c.get("enabled", True)),
        "min_gap_min": int(c.get("min_gap_min", 40)),
        "max_gap_min": int(c.get("max_gap_min", 100)),
        "quiet_from": int(c.get("quiet_from", 23)),
        "quiet_to": int(c.get("quiet_to", 9)),
        "daily_max": int(c.get("daily_max", 6)),
    }


def qzone_cfg() -> dict:
    c = CFG.get("qzone") or {}
    return {
        "enabled": bool(c.get("enabled", False)),
        "per_day": int(c.get("per_day", 1)),
        "from_hour": int(c.get("from_hour", 10)),
        "to_hour": int(c.get("to_hour", 22)),
        "min_gap_min": int(c.get("min_gap_min", 240)),
    }


def _in_quiet(hour: int, cfg: dict) -> bool:
    a, b = cfg["quiet_from"], cfg["quiet_to"]
    if a == b:
        return False
    return (hour >= a or hour < b) if a > b else (a <= hour < b)


def _plan_idle(why: str = "") -> int:
    import random
    cfg = idle_cfg()
    gap = random.randint(cfg["min_gap_min"], max(cfg["min_gap_min"], cfg["max_gap_min"]))
    st = _st_load(IDLE_STATE)
    st["next_at"] = time.time() + gap * 60
    st["why"] = why
    _st_save(IDLE_STATE, st)
    return gap


def idle_status() -> dict:
    cfg = idle_cfg()
    st = _st_load(IDLE_STATE)
    nxt = float(st.get("next_at") or 0)
    return {
        "ok": True, "config": cfg,
        "next_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(nxt)) if nxt else "",
        "next_in_min": max(0, int((nxt - time.time()) / 60)) if nxt else None,
        "last_at": st.get("last_at_text") or "",
        "today_count": int(st.get("today_count") or 0),
        "day": st.get("day") or "",
        "why": st.get("why") or "",
        "mood": mood_state(),
    }


def dsh_generate(instruction: str, max_tokens: int = 160) -> tuple[str, str]:
    """让 DSH 按「此刻的你」说一句话(主动消息 / 说说 / B站评论都用它)。"""
    dsh = CFG.get("dsh") or {}
    mb = mood_brief()
    msgs = [{"role": "system", "content": persona() + ("\n\n" + mb if mb else "")},
            {"role": "user", "content": instruction}]
    try:
        out = qq_model_reply(msgs, max_tokens, float(dsh.get("temperature") or 0.9),
                             float(dsh.get("timeout") or 40))
        return out[:500], ""
    except ModelError as e:
        return "", str(e)


def _owner_chat() -> str:
    return "private:" + str(CFG.get("owner") or "")


def idle_fire(manual: bool = False) -> dict:
    """主动找主人说一句。"""
    if not OB.connected:
        return {"ok": False, "error": "协议端还没连上"}
    owner = str(CFG.get("owner") or "")
    if not owner:
        return {"ok": False, "error": "没配 owner"}
    st = _st_load(IDLE_STATE)
    today = time.strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"] = today
        st["today_count"] = 0
    cfg = idle_cfg()
    if not manual:
        if not cfg["enabled"]:
            return {"ok": False, "error": "主动消息没开"}
        if _in_quiet(time.localtime().tm_hour, cfg):
            return {"ok": False, "error": "现在是安静时段"}
        if int(st.get("today_count") or 0) >= cfg["daily_max"]:
            return {"ok": False, "error": "今天已经说够 %d 次了" % cfg["daily_max"]}
    instruction = ("现在是你自己待着的时候,泥泥没在跟你说话。你突然想起他了,主动发一条消息过去。"
                   "一到两句,用你平常的口气,别提这条要求、别说「系统」「提示」这类字。"
                   "可以问他在干嘛、提醒他喝水、说说你刚在发呆看到的东西。")
    text, err = dsh_generate(instruction, max_tokens=120)
    if err or not text:
        return {"ok": False, "error": err or "模型没返回"}
    try:
        OB.call("send_private_msg", {"user_id": int(owner), "message": text})
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}
    st["last_at"] = time.time()
    st["last_at_text"] = now()
    st["today_count"] = int(st.get("today_count") or 0) + 1
    _st_save(IDLE_STATE, st)
    _plan_idle("发完一条,重新排期")
    AUDIT.add({"time": now(), "chat": _owner_chat(), "sender": "dsh", "sender_name": "DSH",
               "text": text[:300], "decision": "reply", "reason": "主动找他"})
    return {"ok": True, "text": text}


def qzone_fire(manual: bool = False) -> dict:
    """发一条 QQ 空间说说。"""
    cfg = qzone_cfg()
    st = _st_load(QZONE_STATE)
    today = time.strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"] = today
        st["count"] = 0
    if not manual:
        if not cfg["enabled"]:
            return {"ok": False, "error": "自动说说没开"}
        h = time.localtime().tm_hour
        if not (cfg["from_hour"] <= h < cfg["to_hour"]):
            return {"ok": False, "error": "不在发说说的时段"}
        if int(st.get("count") or 0) >= cfg["per_day"]:
            return {"ok": False, "error": "今天已经发过了"}
    instruction = ("写一条 QQ 空间说说,发给你自己看也给别人看:一到两句,口语,别用表情符号堆砌,"
                   "写你此刻的心情或者今天在温室里做的一件小事,不要提「AI」「模型」「系统」。")
    text, err = dsh_generate(instruction, max_tokens=120)
    if err or not text:
        return {"ok": False, "error": err or "模型没返回"}
    try:
        loop = OB.loop or asyncio.new_event_loop()
        ok, msg = asyncio.run_coroutine_threadsafe(qzone.publish(text), loop).result(40)
    except Exception as e:
        return {"ok": False, "error": "发说说失败:%s" % str(e)[:160], "text": text}
    if ok:
        st["count"] = int(st.get("count") or 0) + 1
        st["last_at"] = now()
        st["last_ts"] = time.time()
        st["last_text"] = text
        _st_save(QZONE_STATE, st)
        AUDIT.add({"time": now(), "chat": "qzone", "sender": "dsh", "sender_name": "DSH",
                   "text": text[:300], "decision": "reply", "reason": "发了说说"})
    return {"ok": bool(ok), "msg": msg, "text": text}


def _bg_loops():
    """后台两条排期:主动消息 + 说说。"""
    def idle_loop():
        time.sleep(20)
        _plan_idle("启动排期")
        while True:
            try:
                time.sleep(45)
                cfg = idle_cfg()
                if not cfg["enabled"] or not OB.connected:
                    continue
                if _in_quiet(time.localtime().tm_hour, cfg):
                    continue
                st = _st_load(IDLE_STATE)
                if time.time() < float(st.get("next_at") or 0):
                    continue
                idle_fire()
            except Exception as e:
                print("[qq] idle loop:", e, flush=True)

    def qzone_loop():
        time.sleep(90)
        while True:
            try:
                time.sleep(600)
                cfg = qzone_cfg()
                if not cfg["enabled"] or not OB.connected:
                    continue
                st = _st_load(QZONE_STATE)
                if st.get("day") == time.strftime("%Y-%m-%d") and int(st.get("count") or 0) >= cfg["per_day"]:
                    continue
                h = time.localtime().tm_hour
                if not (cfg["from_hour"] <= h < cfg["to_hour"]):
                    continue
                if random.random() < 0.25:      # 一个时段里随机挑一次,别太机械
                    qzone_fire()
            except Exception as e:
                print("[qq] qzone loop:", e, flush=True)

    threading.Thread(target=idle_loop, daemon=True).start()
    threading.Thread(target=qzone_loop, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "RivuletQQ/2.0"

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
        if u.path == "/api/status":
            return self._send(200, {
                "ok": True, "time": now(),
                "ws": {"connected": OB.connected, "url": CFG.get("ws_url"), "error": OB.error},
                "account": OB.account, "groups": len(OB.groups), "friends": len(OB.friends),
                "napcat": napcat_state(),
                "whitelist": {"enabled": bool(WL.get("enabled")),
                              "users": len(WL.get("users", [])), "groups": len(WL.get("groups", []))},
                "stats": {"allowed": AUDIT.allowed, "blocked": AUDIT.blocked,
                          "replied": AUDIT.replied, "last_at": AUDIT.last_at},
                "last_send_error": OB.last_send_error,
                "reply_enabled": bool(CFG.get("reply_enabled")),
                "group_at_only": bool(CFG.get("group_at_only")),
                **qq_model_status(),
            })
        if u.path == "/api/whitelist":
            return self._send(200, {"ok": True, "enabled": bool(WL.get("enabled")),
                                    "users": WL.get("users", []), "groups": WL.get("groups", [])})
        if u.path == "/api/mood":
            st = mood_state()
            return self._send(200, {"ok": True, "mood": st,
                                    "brief": st.get("brief", ""), "words": st.get("words", {})})
        if u.path == "/api/idle":
            return self._send(200, idle_status())
        if u.path == "/api/qzone":
            st = _st_load(QZONE_STATE)
            return self._send(200, {"ok": True, "config": qzone_cfg(), "state": st})
        if u.path == "/api/conversation":
            n = int((q.get("limit") or ["80"])[0])
            items = [x for x in AUDIT.recent(400) if x.get("decision") in ("allow", "reply", "error")]
            return self._send(200, {"ok": True, "items": items[-n:]})
        if u.path == "/api/audit":
            n = int((q.get("limit") or ["100"])[0])
            return self._send(200, {"ok": True, "items": AUDIT.recent(n)})
        if u.path == "/api/config":
            dsh = CFG.get("dsh") or {}
            return self._send(200, {"ok": True, "config": {
                "ws_url": CFG.get("ws_url"), "account": CFG.get("account"),
                "owner": CFG.get("owner"), "reply_enabled": CFG.get("reply_enabled"),
                "group_at_only": CFG.get("group_at_only"),
                "model": dsh.get("model"), "api_base": dsh.get("api_base"),
                "use_workspace_model": bool(CFG.get("use_workspace_model")),
                "has_key": bool((dsh.get("api_key") or "").strip()),
                "napcat_launcher": CFG.get("napcat_launcher"),
            }})
        if u.path == "/api/qr":
            try:
                qp = _nc().qr()
            except Exception:
                qp = ""
            if qp and os.path.exists(qp):
                data = open(qp, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            return self._send(404, {"ok": False, "error": "还没有二维码,先启动协议端"})
        if u.path == "/api/health":
            return self._send(200, {"ok": True, "service": "rivulet-qq"})
        return self._send(404, {"ok": False, "error": "no such route"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        try:
            if u.path == "/api/whitelist/add":
                kind = body.get("kind") or "user"
                ident = str(body.get("id") or "").strip()
                note = str(body.get("note") or "").strip()[:40]
                if not ident.isdigit():
                    return self._send(422, {"ok": False, "error": "ID 只能是数字 QQ 号 / 群号"})
                key = "users" if kind == "user" else "groups"
                lst = WL.setdefault(key, [])
                if any(str(x.get("id")) == ident for x in lst):
                    return self._send(200, {"ok": True, "duplicate": True})
                lst.append({"id": ident, "note": note, "added_at": now()})
                save_wl()
                return self._send(200, {"ok": True})
            if u.path == "/api/whitelist/remove":
                kind = body.get("kind") or "user"
                ident = str(body.get("id") or "").strip()
                key = "users" if kind == "user" else "groups"
                WL[key] = [x for x in WL.get(key, []) if str(x.get("id")) != ident]
                save_wl()
                return self._send(200, {"ok": True})
            if u.path == "/api/whitelist/enabled":
                WL["enabled"] = bool(body.get("on"))
                save_wl()
                return self._send(200, {"ok": True, "enabled": WL["enabled"]})
            if u.path == "/api/config":
                for k in ("ws_url", "account", "owner"):
                    if k in body and body[k] is not None:
                        CFG[k] = str(body[k]).strip()
                for k in ("reply_enabled", "group_at_only"):
                    if k in body:
                        CFG[k] = bool(body[k])
                for k in ("model", "api_key", "api_base"):
                    if body.get(k):
                        CFG.setdefault("dsh", {})[k] = str(body[k]).strip()
                        CFG["use_workspace_model"] = False
                save_cfg()
                return self._send(200, {"ok": True})
            if u.path == "/api/model/configure":
                return self._send(200, configure_qq_model(body))
            if u.path == "/api/model/use-workspace":
                return self._send(200, use_workspace_model())
            if u.path == "/api/napcat/start":
                return self._send(200, start_napcat())
            if u.path == "/api/napcat/stop":
                return self._send(200, stop_napcat())
            if u.path == "/api/napcat/restart":
                return self._send(200, restart_napcat())
            if u.path == "/api/test_model":
                return self._send(200, test_model())
            if u.path == "/api/idle/trigger":
                return self._send(200, idle_fire(manual=True))
            if u.path == "/api/idle/config":
                c = CFG.setdefault("idle", {})
                for k in ("enabled",):
                    if k in body:
                        c[k] = bool(body[k])
                for k in ("min_gap_min", "max_gap_min", "quiet_from", "quiet_to", "daily_max"):
                    if body.get(k) is not None:
                        c[k] = int(body[k])
                save_cfg()
                return self._send(200, idle_status())
            if u.path == "/api/qzone/post":
                return self._send(200, qzone_fire(manual=True))
            if u.path == "/api/qzone/config":
                c = CFG.setdefault("qzone", {})
                for k in ("enabled",):
                    if k in body:
                        c[k] = bool(body[k])
                for k in ("per_day", "from_hour", "to_hour", "min_gap_min"):
                    if body.get(k) is not None:
                        c[k] = int(body[k])
                save_cfg()
                return self._send(200, {"ok": True, "config": qzone_cfg()})
            if u.path == "/api/persona_changed":
                global PERSONA_EPOCH
                PERSONA_EPOCH = time.time()
                return self._send(200, {"ok": True, "epoch": PERSONA_EPOCH,
                                        "hint": "之后的回复只参考新对话,不再沿用旧人格口气"})
            if u.path == "/api/generate":
                text, err = dsh_generate(str(body.get("prompt") or ""),
                                         int(body.get("max_tokens") or 160))
                if err:
                    return self._send(200, {"ok": False, "error": err})
                return self._send(200, {"ok": True, "text": text})
            if u.path == "/api/test_send":
                chat = str(body.get("chat") or "").strip()
                text = str(body.get("text") or "").strip()
                if not chat or not text:
                    return self._send(422, {"ok": False, "error": "chat / text 都要有"})
                if not OB.connected:
                    return self._send(503, {"ok": False, "error": "协议端还没连上"})
                kind = "group" if chat.startswith("group:") else "private"
                target = chat.split(":", 1)[1]
                OB.call("send_group_msg" if kind == "group" else "send_private_msg",
                        ({"group_id": int(target)} if kind == "group" else {"user_id": int(target)}) | {"message": text})
                AUDIT.add({"time": now(), "chat": chat, "sender": "panel", "sender_name": "面板测试",
                           "text": text[:300], "decision": "reply", "reason": "面板测试发送"})
                return self._send(200, {"ok": True})
            if u.path == "/api/reload":
                if OB.connected:
                    try:
                        r = OB.call("get_group_list", {})
                        OB.groups = (r or {}).get("data") or []
                    except Exception:
                        pass
                return self._send(200, {"ok": True, "groups": len(OB.groups)})
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)})
        return self._send(404, {"ok": False, "error": "no such route"})


def main():
    port = int(os.environ.get("RIVULET_QQ_PORT") or 18096)
    if not os.path.exists(CFG_PATH):
        save_cfg()
    if not os.path.exists(WL_PATH):
        save_wl()
    _preload_audit()
    if not WL.get("users") and CFG.get("owner"):
        WL["users"] = [{"id": str(CFG["owner"]), "note": "主人", "added_at": now()}]
        save_wl()

    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        OB.loop = loop
        loop.create_task(OB.run())
        loop.run_forever()

    threading.Thread(target=run_loop, daemon=True).start()
    _bg_loops()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[qq] http://127.0.0.1:{port} ws={CFG.get('ws_url')} "
          f"wl={len(WL.get('users', []))}u/{len(WL.get('groups', []))}g", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
