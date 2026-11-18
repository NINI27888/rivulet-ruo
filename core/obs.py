# -*- coding: utf-8 -*-
"""可观测性:请求 ID、单行 JSON 日志、内存指标、异常落库、保留策略。

纯标准库 + starlette(本来就是 FastAPI 的依赖),不引新包。

对外入口:
  install(app)            注册中间件(唯一被 main.py 调用的)
  log(ev, **f)            打一行结构化日志
  log_exc(info, exc)      异常:脱敏 + 落库 + 打日志
  metrics()               内存计数器快照(重启清零)
  prune()                 保留策略(启动一次 + 每 200 次写入一次)
其余是内部工具,别从外面调。

时间一律 UTC 毫秒 ISO(`2026-11-03T08:19:20.123Z`),只有本模块用这种格式;
旧表里的 `YYYY-MM-DD HH:MM:SS` 一个都不动。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db

BASE = Path(__file__).resolve().parent

RID_HEADER = "X-Request-Id"
RID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_rid: ContextVar[str] = ContextVar("obs_rid", default="")

_DEFAULTS = {
    "json_log": True,
    "store": True,
    "slow_ms": 1000,
    "keep_days": 30,
    "keep_rows": 5000,
    "debug_endpoints": False,
}


def _load_cfg() -> dict:
    cfg = dict(_DEFAULTS)
    try:
        data = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
        cfg.update(data.get("obs") or {})
        # 唯一开关是 features.obs(3-0-5b 统一);老键 obs.enabled 已移除,写了不认
        cfg.pop("enabled", None)
        feats = data.get("features")
        cfg["features_obs"] = (
            bool(feats["obs"]) if isinstance(feats, dict) and "obs" in feats else True
        )
        cfg["_version"] = data.get("version", "0")
    except Exception:
        cfg["_version"] = "0"
        cfg["features_obs"] = True
    return cfg


CFG = _load_cfg()


def feature_on(config: dict | None = None) -> bool:
    """开关判定:只认 `features.obs`,没写就默认开。老键 `obs.enabled` 已移除(3-0-5b)。

    features.obs=false 的语义(方案 §1):中间件不装、两个接口不挂(404)、
    obs worker(首轮 prune)不启动 —— 整个模块退化成不存在。
    """
    if isinstance(config, dict):
        feats = config.get("features")
        if isinstance(feats, dict) and "obs" in feats:
            return bool(feats["obs"])
    return bool(CFG.get("features_obs", True))


def enabled() -> bool:
    """模块内统一用这个问"我该不该干活"。"""
    return bool(CFG.get("features_obs", True))


# ---------------- 时间 ----------------

def utc_ms() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)


def iso_ago(days: float) -> str:
    now = datetime.now(timezone.utc) - timedelta(days=days)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)


# ---------------- 脱敏 ----------------

_SENSITIVE_KEYS = {
    "password", "passwd", "pwd", "token", "secret", "key", "apikey", "api_key",
    "authorization", "auth", "x-node-token", "cookie", "set-cookie", "session",
}
_HEADER_WHITELIST = {
    "host", "user-agent", "content-type", "content-length",
    "x-request-id", "x-forwarded-for",
}
_SENSITIVE_HEADERS = {"authorization", "x-node-token", "cookie", "set-cookie"}

_RE_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_RE_PBKDF2 = re.compile(r"pbkdf2\$[^\s\"']+")
_RE_HEX32 = re.compile(r"[0-9a-fA-F]{32,}")
# 裸文本里的 key=value / key: value(异常消息、URL、日志都常见这种)
_RE_KV_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|authorization)"
    r"\s*[=:]\s*([^\s&,;\"'}\]]+)"
)


def redact(obj):
    """脱敏统一入口:dict/list/str 递归;其它原样。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in _SENSITIVE_KEYS:
                out[k] = "***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        s = _RE_BEARER.sub("Bearer ***", obj)
        s = _RE_PBKDF2.sub("pbkdf2$***", s)
        s = _RE_KV_SECRET.sub(lambda m: m.group(1) + "=***", s)
        s = _RE_HEX32.sub(lambda m: m.group(0)[:4] + "***", s)
        return s
    return obj


def safe_headers(headers) -> dict:
    """头白名单:其余一律丢弃;敏感头即使出现也只记 ***。"""
    items = headers.items() if hasattr(headers, "items") else headers
    out = {}
    for k, v in items:
        lk = str(k).lower()
        if lk in _HEADER_WHITELIST:
            out[lk] = redact(str(v))[:200]
        elif lk in _SENSITIVE_HEADERS:
            out[lk] = "***"
    return out


def scope_headers(scope) -> dict:
    """把 ASGI scope 里的 [(b'k', b'v')] 摊成小写 dict,不依赖 starlette。"""
    out = {}
    for k, v in scope.get("headers") or []:
        try:
            out[k.decode("latin-1").lower()] = v.decode("latin-1")
        except Exception:
            continue
    return out


# ---------------- 日志 ----------------

def _write_line(line: str) -> None:
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def log(ev: str, **fields) -> None:
    """一行 JSON 打 stdout。字段一律先脱敏。"""
    rid = _rid.get()
    rec = {"ts": utc_ms(), "lvl": fields.pop("lvl", "info"), "ev": ev}
    if rid:
        rec["rid"] = rid
    rec.update(redact(fields))
    rec.setdefault("ver", CFG.get("_version", "0"))
    if CFG.get("json_log", True):
        _write_line(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
    else:
        _write_line("[%s] %s %s" % (rec["lvl"], ev, json.dumps(rec, ensure_ascii=False)))


# ---------------- 指标(内存,重启清零) ----------------

_LOCK = threading.Lock()
_STARTED = time.time()
_BY_STATUS = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
_BY_PATH: dict = {}
_REQUESTS = 0
_ERRORS = 0
_SLOW = 0
_ERROR_TS = deque(maxlen=2000)
_SLOW_TS = deque(maxlen=2000)


def _bucket(st: int) -> str:
    if st >= 500:
        return "5xx"
    if st >= 400:
        return "4xx"
    if st >= 300:
        return "3xx"
    return "2xx"


def _count(path: str, st: int, ms: int) -> None:
    global _REQUESTS, _ERRORS, _SLOW
    now = time.time()
    with _LOCK:
        _REQUESTS += 1
        _BY_STATUS[_bucket(st)] += 1
        slot = _BY_PATH.get(path)
        if slot is None:
            _BY_PATH[path] = [1, ms]
        else:
            slot[0] += 1
            slot[1] += ms
        if st >= 500:
            _ERRORS += 1
            _ERROR_TS.append(now)
        if ms >= int(CFG.get("slow_ms", 1000)):
            _SLOW += 1
            _SLOW_TS.append(now)


def _recent(ts_deque) -> int:
    cut = time.time() - 3600
    return sum(1 for t in ts_deque if t >= cut)


def metrics() -> dict:
    with _LOCK:
        by_path = sorted(
            ({"path": p, "n": v[0], "avg_ms": int(round(v[1] / v[0])) if v[0] else 0}
             for p, v in _BY_PATH.items()),
            key=lambda x: (-x["n"], x["path"]),
        )[:50]
        return {
            "uptime_s": round(time.time() - _STARTED, 1),
            "pid": os.getpid(),
            "version": CFG.get("_version", "0"),
            "requests_total": _REQUESTS,
            "by_status": dict(_BY_STATUS),
            "by_path": by_path,
            "errors_1h": _recent(_ERROR_TS),
            "errors_total": _ERRORS,
            "slow_1h": _recent(_SLOW_TS),
            "slow_total": _SLOW,
        }


# ---------------- 落库 ----------------

_WRITE_COUNT = 0
_STORE_WARNED = False
_STORE_LOCK = threading.Lock()

_COLS = ("kind", "ts", "rid", "method", "path", "status", "dur_ms", "uid",
         "username", "ip", "exc_type", "exc_msg", "traceback", "extra_json")


def store(kind: str, **f) -> None:
    """写一行 obs_events。表没建/库坏了都不抛,只打一行日志。"""
    global _WRITE_COUNT, _STORE_WARNED
    if not enabled():
        return
    if not CFG.get("store", True):
        return
    rec = {
        "kind": kind,
        "ts": utc_ms(),
        "rid": f.get("rid") or _rid.get() or "",
        "method": f.get("method"),
        "path": f.get("path"),
        "status": f.get("status"),
        "dur_ms": f.get("dur_ms"),
        "uid": f.get("uid"),
        "username": f.get("username"),
        "ip": f.get("ip"),
        "exc_type": f.get("exc_type"),
        "exc_msg": f.get("exc_msg"),
        "traceback": f.get("traceback"),
        "extra_json": json.dumps(redact(f.get("extra") or {}), ensure_ascii=False),
    }
    try:
        c = db.conn()
        try:
            c.execute(
                "INSERT INTO obs_events(%s) VALUES (%s)"
                % (",".join(_COLS), ",".join("?" * len(_COLS))),
                tuple(rec[k] for k in _COLS),
            )
            c.commit()
        finally:
            c.close()
    except Exception as exc:
        if not _STORE_WARNED:
            _STORE_WARNED = True
            log("obs.store_failed", lvl="warn", err=repr(exc)[:300],
                hint="表不存在或库不可写;003_obs.sql 建表后自动恢复")
        return
    with _STORE_LOCK:
        _WRITE_COUNT += 1
        due = _WRITE_COUNT % 200 == 0
    if due:
        prune()


def prune() -> dict:
    """保留策略:先按天数删,再把超出 keep_rows 的最旧行删掉。只碰 obs_events。"""
    days = int(CFG.get("keep_days", 30))
    rows = int(CFG.get("keep_rows", 5000))
    out = {"deleted_by_age": 0, "deleted_by_rows": 0, "keep_days": days, "keep_rows": rows}
    t0 = time.perf_counter()
    try:
        c = db.conn()
        try:
            cur = c.execute("DELETE FROM obs_events WHERE ts < ?", (iso_ago(days),))
            out["deleted_by_age"] = max(cur.rowcount or 0, 0)
            cur = c.execute(
                "DELETE FROM obs_events WHERE id NOT IN "
                "(SELECT id FROM obs_events ORDER BY id DESC LIMIT ?)", (rows,))
            out["deleted_by_rows"] = max(cur.rowcount or 0, 0)
            c.commit()
        finally:
            c.close()
    except Exception as exc:
        out["error"] = repr(exc)[:200]
    out["ms"] = int(round((time.perf_counter() - t0) * 1000))
    return out


# ---------------- 请求信息 ----------------

def new_rid() -> str:
    return "req_" + secrets.token_hex(8)


def pick_rid(raw) -> str:
    if raw and RID_RE.match(raw):
        return raw
    return new_rid()


def _client_ip(scope) -> str:
    client = scope.get("client") or ("", 0)
    return client[0] or ""


def info_from_scope(scope) -> dict:
    state = scope.get("state") or {}
    return {
        "rid": state.get("rid") or _rid.get(),
        "method": scope.get("method", ""),
        "path": scope.get("path", ""),
        "ip": _client_ip(scope),
    }


def log_access(info: dict, status: int, dur_ms: int, uid=None, username=None,
               headers=None, do_store: bool = True) -> None:
    _count(info.get("path", ""), status, dur_ms)
    slow_ms = int(CFG.get("slow_ms", 1000))
    lvl = "info"
    if status >= 500:
        lvl = "error"
    elif status >= 400 or dur_ms >= slow_ms:
        lvl = "warn"
    fields = {
        "lvl": lvl, "m": info.get("method"), "p": info.get("path"),
        "st": status, "ms": dur_ms, "uid": uid, "ip": info.get("ip"),
    }
    if username:
        fields["username"] = username
    if headers is not None:
        fields["h"] = safe_headers(headers)
    log("http.access", **fields)
    if not do_store:
        return
    if status >= 500:
        store("error", **info, status=status, dur_ms=dur_ms, uid=uid,
              username=username, extra={"phase": "response"})
    elif status >= 400:
        store("http_error", **info, status=status, dur_ms=dur_ms, uid=uid, username=username)
    elif status >= 200 and dur_ms >= slow_ms:
        store("slow", **info, status=status, dur_ms=dur_ms, uid=uid, username=username)


def log_exc(info: dict, exc: BaseException, status: int = 500, dur_ms=None) -> str:
    """异常前三层里的前两层:日志 + 库。返回 rid,供响应体回填。"""
    if not enabled():
        return ""
    rid = info.get("rid") or _rid.get() or ""
    tb = redact(traceback.format_exc())[:8192]
    msg = redact(str(exc))[:1024]
    log("http.error", lvl="error", rid=rid, m=info.get("method"), p=info.get("path"),
        exc_type=type(exc).__name__, exc_msg=msg, traceback=tb)
    store("error", rid=rid, method=info.get("method"), path=info.get("path"),
          status=status, dur_ms=dur_ms, ip=info.get("ip"),
          exc_type=type(exc).__name__, exc_msg=msg, traceback=tb,
          extra={"phase": "unhandled"})
    return rid


# ---------------- 中间件 ----------------

_STARTED_ONCE = False
_START_LOCK = threading.Lock()
# worker 状态:目前 worker 只做启动 prune + 每 200 次写入的 prune;
# features.obs=false 时永远 started=False(接口 404、中间件不装、没人调 _ensure_started)。
_WORKER = {"started": False}


def worker_status() -> dict:
    return {"started": bool(_WORKER["started"]), "feature_obs": enabled()}


def _ensure_started() -> None:
    """第一笔请求进来时跑一次启动动作(目前只有 prune)。

    不做 lifespan 钩子,是为了 main.py 只加一行 install(app);
    prune 只碰 obs_events,几千行也只要几毫秒。
    features.obs=false 时**不启动**:不 prune、不建 worker 状态。
    """
    global _STARTED_ONCE
    if _STARTED_ONCE:
        return
    if not enabled():
        return
    with _START_LOCK:
        if _STARTED_ONCE:
            return
        _STARTED_ONCE = True
        _WORKER["started"] = True
        out = prune()
        log("obs.start", **out)


class _ObsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        _ensure_started()

        headers = scope_headers(scope)
        rid = pick_rid(headers.get(RID_HEADER.lower()))
        token = _rid.set(rid)
        state = scope.setdefault("state", {})
        state["rid"] = rid
        t0 = time.perf_counter()
        holder = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                holder["status"] = message["status"]
                raw = list(message.get("headers") or [])
                raw.append((b"x-request-id", rid.encode("latin-1")))
                message["headers"] = raw
            await send(message)

        info = info_from_scope(scope)
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # 不在这里记:异常会往上抛给 main._unhandled,那边有完整 traceback。
            # 这里只留个标记,保证同一个请求不会落两行。
            state["obs_exc_seen"] = True
            state["obs_exc_logged"] = True
            raise
        finally:
            dur = int(round((time.perf_counter() - t0) * 1000))
            st = holder["status"]
            # 给 _unhandled 用:异常那一行也能带上耗时,不用再算一遍
            state["dur_ms"] = dur
            state["status"] = st
            # 5xx 由 _unhandled -> log_exc 落过库的,不再重复写一行
            do_store = not state.get("obs_exc_logged")
            log_access(
                info, st, dur,
                uid=state.get("uid"), username=state.get("username"),
                headers=headers if st >= 400 else None,
                do_store=do_store,
            )
            _rid.reset(token)


def install(app, config: dict | None = None, apply: bool = True) -> None:
    """唯一入口:注册中间件。features.obs=false 时整层不装(什么都不做)。

    config 传进来时以它为准,并顺手覆盖 json_log/store/slow_ms/keep_days/
    keep_rows/debug_endpoints —— 免得模块加载时读到的和 main.py 的不一致。
    apply=False 只做局部判定、不改模块级 CFG(自测用,免得一次关开关把后续用例带偏)。
    """
    if isinstance(config, dict) and apply:
        CFG["features_obs"] = feature_on(config)
        o = config.get("obs") or {}
        for k in ("json_log", "store", "slow_ms", "keep_days", "keep_rows",
                  "debug_endpoints"):
            if k in o:
                CFG[k] = o[k]
    on = feature_on(config) if isinstance(config, dict) else enabled()
    if not on:
        log("obs.disabled", features_obs=False)
        return
    app.add_middleware(_ObsMiddleware)
    log("obs.installed", enabled=True, store=bool(CFG.get("store", True)),
        slow_ms=int(CFG.get("slow_ms", 1000)))
