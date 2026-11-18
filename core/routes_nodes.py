# -*- coding: utf-8 -*-
"""节点接口:注册、心跳/取命令、回结果、列表。"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

import auth
from auth import current_node, current_user, db_conn

router = APIRouter(prefix="/api/node", tags=["node"])


class RegisterIn(BaseModel):
    hostname: str = Field("", max_length=128)
    platform: str = Field("", max_length=64)
    app_version: str = Field("", max_length=32)
    meta: Optional[dict] = None


class ResultIn(BaseModel):
    command_id: int
    status: str = Field("done", pattern="^(done|failed|cancelled)$")
    result: str = Field("", max_length=20000)


# 长轮询:最多挂 25 秒;投递出去超过这么久还没结果就允许重发(节点端有 ID 去重,重发不会重复执行)
POLL_WAIT_MAX = 25
POLL_STEP = 0.5
REDELIVER_AFTER_S = 120
MAX_ATTEMPTS = 3


def _online(last_seen: Optional[str]) -> bool:
    if not last_seen:
        return False
    now = datetime.now(timezone.utc)
    try:
        seen = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now - seen).total_seconds() <= auth.ONLINE_WINDOW_S


@router.post("/register")
def register(
    body: RegisterIn,
    request: Request,
    user: dict = Depends(current_user),
    c: sqlite3.Connection = Depends(db_conn),
) -> dict:
    node_id = "nd_" + uuid.uuid4().hex[:12]
    token = secrets.token_urlsafe(24)
    ip = request.client.host if request.client else ""
    c.execute(
        "INSERT INTO nodes(id, hostname, platform, app_version, token, status, ip, last_ip, meta, user_id, last_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            node_id,
            body.hostname,
            body.platform,
            body.app_version,
            token,
            "offline",
            ip,
            ip,
            json.dumps(body.meta or {}, ensure_ascii=False),
            user["id"],
            auth.now_iso(),
        ),
    )
    c.commit()
    auth.audit(c, user["username"], "node_registered", node_id, body.hostname)
    return {"ok": True, "node": {"id": node_id, "token": token, "hostname": body.hostname}}


@router.post("/poll")
def poll(
    request: Request,
    wait: int = Query(0, ge=0, le=POLL_WAIT_MAX),
    node: dict = Depends(current_node),
    c: sqlite3.Connection = Depends(db_conn),
) -> dict:
    """取命令(同时算心跳)。wait>0 时做长轮询:没命令就挂住,最多 wait 秒。"""
    ip = request.client.host if request.client else ""
    deadline = time.time() + wait
    last_beat = 0.0
    while True:
        now = auth.now_iso()

        # 心跳:每 5 秒刷一次,免得长轮询期间被判离线
        if wait == 0 or time.time() - last_beat >= 5:
            c.execute(
                "UPDATE nodes SET last_seen = ?, last_ip = ?, status = 'online' WHERE id = ?",
                (now, ip, node["id"]),
            )
            last_beat = time.time()

        # 投递超时没回音的,退回 pending 允许重发(节点端按 ID 去重,不会重复执行)
        c.execute(
            "UPDATE commands SET status = 'pending', updated_at = ? "
            "WHERE node_id = ? AND status = 'delivered' AND attempts < ? "
            "AND delivered_at IS NOT NULL AND delivered_at < ?",
            (now, node["id"], MAX_ATTEMPTS, auth.now_iso(-REDELIVER_AFTER_S / 3600.0)),
        )
        c.commit()

        rows = c.execute(
            "SELECT id, kind, payload, attempts FROM commands "
            "WHERE node_id = ? AND status = 'pending' ORDER BY id LIMIT 10",
            (node["id"],),
        ).fetchall()

        if rows:
            ids = [r["id"] for r in rows]
            marks = ",".join("?" * len(ids))
            c.execute(
                "UPDATE commands SET status = 'delivered', delivered_at = ?, "
                "attempts = attempts + 1, updated_at = ? WHERE id IN (%s)" % marks,
                [now, now] + ids,
            )
            c.commit()
            out = []
            for r in rows:
                try:
                    payload = json.loads(r["payload"]) if r["payload"] else None
                except ValueError:
                    payload = r["payload"]
                out.append({"id": r["id"], "kind": r["kind"], "payload": payload})
            return {"ok": True, "server_time": now, "commands": out, "count": len(out),
                    "waited": round(max(0.0, time.time() - (deadline - wait)), 2)}

        if time.time() >= deadline:
            return {"ok": True, "server_time": now, "commands": [], "count": 0,
                    "waited": wait}
        time.sleep(POLL_STEP)


@router.get("/cmd/{cmd_id}")
def node_cmd_status(
    cmd_id: int,
    node: dict = Depends(current_node),
    c: sqlite3.Connection = Depends(db_conn),
) -> dict:
    """节点查自己某条命令的当前状态(执行中用来发现「已取消」)。"""
    row = c.execute(
        "SELECT id, status, attempts, node_id FROM commands WHERE id = ?", (cmd_id,)
    ).fetchone()
    if not row or row["node_id"] != node["id"]:
        raise HTTPException(status_code=404, detail="命令不存在")
    return {"ok": True, "command": {"id": row["id"], "status": row["status"],
                                    "attempts": row["attempts"]}}


@router.post("/result")
def result(
    body: ResultIn,
    node: dict = Depends(current_node),
    c: sqlite3.Connection = Depends(db_conn),
) -> dict:
    row = c.execute(
        "SELECT id, node_id, status FROM commands WHERE id = ?", (body.command_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="命令不存在")
    if row["node_id"] != node["id"]:
        raise HTTPException(status_code=403, detail="这条命令不是发给你的")
    now = auth.now_iso()
    # 已经被用户取消的,不允许节点用 done/failed 把它改回去(只更新结果文本)
    new_status = "cancelled" if row["status"] == "cancelled" else body.status
    c.execute(
        "UPDATE commands SET status = ?, result = ?, updated_at = ? WHERE id = ?",
        (new_status, body.result, now, body.command_id),
    )
    c.commit()
    auth.audit(c, "node:" + node["id"], "command_result", str(body.command_id), new_status)
    return {"ok": True, "status": new_status}


@router.get("/me")
def node_me(node: dict = Depends(current_node)) -> dict:
    data = dict(node)
    data.pop("token", None)
    data["online"] = True
    return {"ok": True, "node": data}


# ---------- 用户视角 ----------

user_router = APIRouter(prefix="/api/nodes", tags=["nodes"])


@user_router.get("")
def list_nodes(
    user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)
) -> dict:
    rows = c.execute("SELECT * FROM nodes ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("token", None)
        d["online"] = _online(d.get("last_seen"))
        out.append(d)
    return {"ok": True, "nodes": out, "count": len(out)}


@user_router.post("/{node_id}/reset-token")
def reset_token(
    node_id: str, user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)
) -> dict:
    token = secrets.token_urlsafe(24)
    cur = c.execute("UPDATE nodes SET token = ? WHERE id = ?", (token, node_id))
    c.commit()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="节点不存在")
    auth.audit(c, user["username"], "node_token_reset", node_id)
    return {"ok": True, "node_id": node_id, "token": token}


@user_router.delete("/{node_id}")
def delete_node(
    node_id: str, user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)
) -> dict:
    c.execute("DELETE FROM commands WHERE node_id = ?", (node_id,))
    cur = c.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
    c.commit()
    auth.audit(c, user["username"], "node_deleted", node_id)
    return {"ok": True, "deleted": cur.rowcount}
