# -*- coding: utf-8 -*-
"""可观测性接口(全 admin;只有 features.obs 开时才挂载,关掉即 404)。

两个正式接口(方案 §2.5,3-0-5 按工单把第 2 个从 errors 定名为 events):
  GET /api/obs/metrics  内存指标(obs.metrics())+ 库概况 {tables, counts}
  GET /api/obs/events   obs_events 查询:limit/kind/path/rid/since/until
                        → {ok, items, total, limit, since, until}

两个调试端点(默认关,obs.debug_endpoints=true 才挂):
  GET /api/obs/_boom            故意抛异常,验 500 三层留痕
  GET /api/obs/_sleep?ms=1200   故意睡,验 slow 落库
调试端点同样仅 admin,**不登记进 GET /**。

安全约束(3-0-5 工单 3/4):
  - 出参走字段白名单,不 SELECT *;
  - traceback 只回前 20 行 / 4000 字符,并过 obs.redact();
  - exc_msg 脱敏后截 512 字符;ip 只留网段;extra 递归脱敏;
  - limit 1..500;since/until 必须 ISO8601,跨度 ≤ 90 天;默认最近 7 天。

不改 obs.py 里的逻辑:本文件只读它的 metrics() / redact(),写库仍归中间件。
"""
from __future__ import annotations

import ipaddress
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

import obs
from auth import current_user, db_conn

router = APIRouter(prefix="/api/obs", tags=["obs"])
debug_router = APIRouter(prefix="/api/obs", tags=["obs-debug"])

# 库概况里关心的几张表(表在才计数,不在就 None)
_COUNT_TABLES = ("users", "nodes", "commands", "sessions", "tasks", "audit_log", "obs_events")

# 出参白名单:数据库里有 16 列,对外只给这些
_OUT_COLS = (
    "id", "ts", "kind", "rid", "method", "path", "status", "dur_ms",
    "uid", "username", "ip", "exc_type", "exc_msg", "traceback",
    "extra_json", "app_version",
)

_MAX_SPAN_DAYS = 90
_DEFAULT_SPAN_DAYS = 7
_TB_MAX_LINES = 20
_TB_MAX_CHARS = 4000


def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _db_brief(c: sqlite3.Connection) -> dict:
    """库里有哪些表 + 关心那几张各多少行。读不到就留 None,不抛。"""
    try:
        tables = [
            r["name"]
            for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        counts = {}
        for name in _COUNT_TABLES:
            try:
                counts[name] = c.execute("SELECT COUNT(*) AS n FROM %s" % name).fetchone()["n"]
            except sqlite3.Error:
                counts[name] = None
        return {"tables": tables, "counts": counts}
    except sqlite3.Error as exc:
        return {"tables": [], "counts": {}, "error": obs.redact(str(exc))[:300]}


# ---------- 时间与脱敏小工具 ----------

def _parse_ts(name: str, value: str) -> datetime:
    """ISO8601 → aware UTC。空串不进来(调用方先判)。"""
    t = (value or "").strip()
    if not t:
        raise HTTPException(status_code=422, detail="%s 不能为空" % name)
    if t.endswith("Z") or t.endswith("z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="%s 要 ISO8601(例如 2026-11-03T06:00:00.000Z)" % name,
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def _clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "…(已截断)"


def _safe_traceback(tb: str) -> str | None:
    """脱敏 + 只留前 20 行 / 4000 字符,不无条件回完整 traceback。"""
    if not tb:
        return None
    lines = obs.redact(tb).splitlines()
    cut = "\n".join(lines[:_TB_MAX_LINES])
    if len(lines) > _TB_MAX_LINES:
        cut += "\n…(共 %d 行,只回前 %d 行)" % (len(lines), _TB_MAX_LINES)
    return _clip(cut, _TB_MAX_CHARS)


def _mask_ip(ip: str | None) -> str | None:
    """只留网段:1.2.3.4 → 1.2.*.*;IPv6 留前两组。不是 IP 就整体打码。"""
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "***"
    if addr.version == 4:
        p = str(addr).split(".")
        return "%s.%s.*.*" % (p[0], p[1])
    p = str(addr).split(":")
    return ":".join(p[:2]) + ":*"


def _out_item(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        extra = json.loads(d.pop("extra_json", None) or "{}")
    except Exception:
        extra = {}
    return {
        "id": d.get("id"),
        "ts": d.get("ts"),
        "kind": d.get("kind"),
        "rid": d.get("rid"),
        "method": d.get("method"),
        "path": d.get("path"),
        "status": d.get("status"),
        "dur_ms": d.get("dur_ms"),
        "uid": d.get("uid"),
        "username": d.get("username"),
        "ip": _mask_ip(d.get("ip")),
        "exc_type": d.get("exc_type"),
        "exc_msg": _clip(obs.redact(d.get("exc_msg") or ""), 512) or None,
        "traceback": _safe_traceback(d.get("traceback")),
        "extra": obs.redact(extra),
        "app_version": d.get("app_version"),
    }


# ---------- 接口 ----------

@router.get("/metrics")
def get_metrics(
    user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)
) -> dict:
    _require_admin(user)
    out = obs.metrics()
    out["ok"] = True
    out["db"] = _db_brief(c)
    out["features"] = {"obs": obs.enabled()}
    return out


@router.get("/events")
def get_events(
    limit: int = Query(50, ge=1, le=500),
    kind: str = Query("", max_length=32),
    path: str = Query("", max_length=300),
    rid: str = Query("", max_length=64),
    since: str = Query("", max_length=40),
    until: str = Query("", max_length=40),
    user: dict = Depends(current_user),
    c: sqlite3.Connection = Depends(db_conn),
) -> dict:
    """只查不删。默认最近 7 天;since/until 跨度最多 90 天,limit 最多 500。"""
    _require_admin(user)

    end = _parse_ts("until", until) if until.strip() else datetime.now(timezone.utc)
    if since.strip():
        start = _parse_ts("since", since)
    else:
        start = end - timedelta(days=_DEFAULT_SPAN_DAYS)
    if start > end:
        raise HTTPException(status_code=422, detail="since 不能晚于 until")
    if (end - start) > timedelta(days=_MAX_SPAN_DAYS):
        raise HTTPException(
            status_code=422,
            detail="时间跨度最多 %d 天(现在是 %.1f 天)"
            % (_MAX_SPAN_DAYS, (end - start).total_seconds() / 86400.0),
        )

    where = ["ts >= ?", "ts <= ?"]
    args: list = [_fmt_ts(start), _fmt_ts(end)]
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if path:
        where.append("path = ?")
        args.append(path)
    if rid:
        where.append("rid = ?")
        args.append(rid)

    tail = " WHERE " + " AND ".join(where)

    # 先确认表在:不在就 503,不假装是空结果
    try:
        c.execute("SELECT 1 FROM obs_events LIMIT 1").fetchall()
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="obs_events 查不了:%s" % obs.redact(str(exc))[:200]
        )

    try:
        total = c.execute("SELECT COUNT(*) AS n FROM obs_events" + tail, args).fetchone()["n"]
        rows = c.execute(
            "SELECT %s FROM obs_events" % ",".join(_OUT_COLS)
            + tail + " ORDER BY id DESC LIMIT ?",
            args + [int(limit)],
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="obs_events 查不了:%s" % obs.redact(str(exc))[:200]
        )

    return {
        "ok": True,
        "items": [_out_item(r) for r in rows],
        "total": total,
        "limit": int(limit),
        "since": _fmt_ts(start),
        "until": _fmt_ts(end),
    }


# ---------- 调试端点(默认不挂;仅 admin;不登记进 GET /) ----------

@debug_router.get("/_boom")
def debug_boom(user: dict = Depends(current_user)) -> dict:
    _require_admin(user)
    raise RuntimeError("obs debug: boom(故意抛的,用来验 500 留痕)")


@debug_router.get("/_sleep")
def debug_sleep(
    ms: int = Query(1200, ge=0, le=10000), user: dict = Depends(current_user)
) -> dict:
    _require_admin(user)
    t0 = time.time()
    time.sleep(ms / 1000.0)
    return {"ok": True, "slept_ms": int(round((time.time() - t0) * 1000))}


# ---------- 挂载 ----------

def mount(app, config: dict) -> None:
    """main.py 的唯一入口:只认 features.obs 决定挂不挂。"""
    if not obs.feature_on(config):
        return
    app.include_router(router)
    cfg = (config or {}).get("obs") or {}
    if cfg.get("debug_endpoints", False):
        app.include_router(debug_router)
        obs.log("obs.debug_enabled", endpoints=["/api/obs/_boom", "/api/obs/_sleep"])
