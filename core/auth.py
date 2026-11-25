# -*- coding: utf-8 -*-
"""口令哈希、会话令牌、鉴权依赖。只用标准库,不引第三方密码库。

- 口令:PBKDF2-HMAC-SHA256,120000 轮,存 `pbkdf2$rounds$salt$hex`
- 用户令牌:随机 32 字节,存 sessions 表,默认 72 小时
- 节点令牌:随机 24 字节,存 nodes.token,长期有效(可重置)
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

import db
import obs

PBKDF2_ROUNDS = 120_000
SESSION_HOURS = 72
ONLINE_WINDOW_S = 90


# ---------- 口令 ----------

def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(8)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), PBKDF2_ROUNDS
    )
    return "pbkdf2$%d$%s$%s" % (PBKDF2_ROUNDS, salt, dk.hex())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$", 3)
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("ascii"), int(rounds)
        )
    except Exception:
        return False
    return hmac.compare_digest(dk.hex(), digest)


# ---------- 时间 ----------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def now_iso(offset_hours: float = 0) -> str:
    return iso(utc_now() + timedelta(hours=offset_hours))


# ---------- 会话 ----------

def issue_session(c: sqlite3.Connection, user_id: int) -> dict:
    token = secrets.token_urlsafe(32)
    expires_at = now_iso(SESSION_HOURS)
    c.execute(
        "INSERT INTO sessions(token, user_id, expires_at) VALUES (?,?,?)",
        (token, user_id, expires_at),
    )
    c.commit()
    return {"token": token, "expires_at": expires_at, "expires_in_s": SESSION_HOURS * 3600}


def revoke_session(c: sqlite3.Connection, token: str) -> None:
    c.execute("DELETE FROM sessions WHERE token = ?", (token,))
    c.commit()


# ---------- 依赖 ----------

def db_conn():
    c = db.conn()
    try:
        yield c
    finally:
        c.close()


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


def current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
    c: sqlite3.Connection = Depends(db_conn),
) -> dict:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    row = c.execute(
        "SELECT s.token, s.expires_at, u.id, u.username, u.role, u.disabled "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
        (token,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="invalid token")
    if row["expires_at"] <= now_iso():
        c.execute("DELETE FROM sessions WHERE token = ?", (token,))
        c.commit()
        raise HTTPException(status_code=401, detail="token expired")
    if row["disabled"]:
        raise HTTPException(status_code=403, detail="user disabled")
    # 管理员只允许本机:防止 token 被复制到别的机器后仍以 admin 身份调用接口。
    if row["role"] == "admin":
        ip = request.client.host if request.client else ""
        if ip not in ("127.0.0.1", "::1", "localhost", ""):
            obs.log("admin.remote_blocked", lvl="warn", user=row["username"], ip=ip)
            raise HTTPException(status_code=403, detail="admin local only")
    c.execute("UPDATE sessions SET last_used = ? WHERE token = ?", (now_iso(), token))
    c.commit()
    # 回填给 obs 中间件:访问日志/异常行都能带上是哪个用户
    request.state.uid = row["id"]
    request.state.username = row["username"]
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "token": token,
    }


def current_node(
    x_node_token: Optional[str] = Header(None),
    c: sqlite3.Connection = Depends(db_conn),
) -> dict:
    if not x_node_token:
        raise HTTPException(status_code=401, detail="missing node token")
    row = c.execute("SELECT * FROM nodes WHERE token = ?", (x_node_token,)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="invalid node token")
    return dict(row)


def audit(c: sqlite3.Connection, actor: str, action: str, target: str = "", note: str = "") -> None:
    try:
        c.execute(
            "INSERT INTO audit_log(actor, action, target, note, created_at) VALUES (?,?,?,?,?)",
            (actor, action, target, note, now_iso()),
        )
        c.commit()
    except Exception as exc:
        # 审计写失败要留痕(仍然不抛):以前这里是 pass,排查时会以为「这件事没发生」
        try:
            obs.log("audit.failed", lvl="warn", actor=actor, action=action,
                    target=target, err=repr(exc)[:300])
        except Exception:
            pass
