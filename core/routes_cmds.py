# -*- coding: utf-8 -*-
"""命令接口:下发、查询、取消、统计。"""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import auth
from auth import current_user, db_conn

router = APIRouter(prefix="/api/cmd", tags=["cmd"])

MAX_PAYLOAD = 20000


class CmdIn(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=64)
    kind: str = Field(..., min_length=1, max_length=64)
    payload: Optional[object] = None


@router.post("")
def create_cmd(
    body: CmdIn, user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)
) -> dict:
    node = c.execute("SELECT id FROM nodes WHERE id = ?", (body.node_id,)).fetchone()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    payload = "" if body.payload is None else json.dumps(body.payload, ensure_ascii=False)
    if len(payload) > MAX_PAYLOAD:
        raise HTTPException(status_code=413, detail="payload 太大")
    now = auth.now_iso()
    cur = c.execute(
        "INSERT INTO commands(node_id, kind, payload, status, created_at, updated_at, created_by) "
        "VALUES (?,?,?,'pending',?,?,?)",
        (body.node_id, body.kind, payload, now, now, user["id"]),
    )
    c.commit()
    auth.audit(c, user["username"], "cmd_created", str(cur.lastrowid), body.kind)
    return {"ok": True, "id": cur.lastrowid, "status": "pending"}


@router.get("")
def list_cmds(
    node_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(20, ge=1, le=200),
    user: dict = Depends(current_user),
    c: sqlite3.Connection = Depends(db_conn),
) -> dict:
    sql = "SELECT id, node_id, kind, payload, status, result, attempts, created_at, updated_at, delivered_at FROM commands WHERE 1=1"
    args = []
    if node_id:
        sql += " AND node_id = ?"
        args.append(node_id)
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = c.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"]) if d["payload"] else None
        except ValueError:
            pass
        out.append(d)
    return {"ok": True, "commands": out, "count": len(out)}


@router.get("/stats")
def stats(user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)) -> dict:
    by_status = {}
    for r in c.execute("SELECT status, COUNT(*) AS n FROM commands GROUP BY status"):
        by_status[r["status"]] = r["n"]
    by_kind = {}
    for r in c.execute("SELECT kind, COUNT(*) AS n FROM commands GROUP BY kind"):
        by_kind[r["kind"]] = r["n"]
    nodes = c.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()["n"]
    return {"ok": True, "commands_by_status": by_status, "commands_by_kind": by_kind, "nodes": nodes}


@router.post("/{cmd_id}/cancel")
def cancel_cmd(
    cmd_id: int, user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)
) -> dict:
    row = c.execute("SELECT status FROM commands WHERE id = ?", (cmd_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="命令不存在")
    if row["status"] in ("done", "failed", "cancelled"):
        return {"ok": True, "changed": 0, "status": row["status"]}
    c.execute(
        "UPDATE commands SET status = 'cancelled', updated_at = ? WHERE id = ?",
        (auth.now_iso(), cmd_id),
    )
    c.commit()
    auth.audit(c, user["username"], "cmd_cancelled", str(cmd_id))
    return {"ok": True, "changed": 1, "status": "cancelled"}
