# -*- coding: utf-8 -*-
"""账号接口:登录、登出、我是谁、改密码、用户管理(admin)。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import auth
from auth import current_user, db_conn

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 管理员账号只允许本机登录:即使将来 core 被改成对外监听、或 token 被复制到
# 别的机器,admin 身份也不会在远端生效。
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", ""}


def _is_local(request: Request) -> bool:
    ip = request.client.host if request.client else ""
    return ip in LOCAL_HOSTS


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class PasswordIn(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class UserIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field("user", pattern="^(user|admin)$")


@router.post("/login")
def login(body: LoginIn, request: Request, c: sqlite3.Connection = Depends(db_conn)) -> dict:
    row = c.execute(
        "SELECT id, username, pass_hash, role, disabled FROM users WHERE username = ?",
        (body.username.strip(),),
    ).fetchone()
    if not row or not auth.verify_password(body.password, row["pass_hash"]):
        auth.audit(c, body.username.strip() or "-", "login_failed")
        raise HTTPException(status_code=401, detail="用户名或密码不对")
    if row["disabled"]:
        raise HTTPException(status_code=403, detail="账号已停用")
    # 管理员账号只允许本机登录
    if row["role"] == "admin" and not _is_local(request):
        auth.audit(c, row["username"], "login_blocked", note="admin 仅限本机")
        raise HTTPException(status_code=403, detail="管理员账号只能在本机登录")
    session = auth.issue_session(c, row["id"])
    c.execute("UPDATE users SET last_login = ? WHERE id = ?", (auth.now_iso(), row["id"]))
    c.commit()
    auth.audit(c, row["username"], "login_ok", note="session issued")
    return {
        "ok": True,
        "token": session["token"],
        "expires_at": session["expires_at"],
        "expires_in_s": session["expires_in_s"],
        "user": {"id": row["id"], "username": row["username"], "role": row["role"]},
    }


@router.post("/logout")
def logout(user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)) -> dict:
    auth.revoke_session(c, user["token"])
    auth.audit(c, user["username"], "logout")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)) -> dict:
    row = c.execute(
        "SELECT id, username, role, created_at, last_login FROM users WHERE id = ?",
        (user["id"],),
    ).fetchone()
    return {"ok": True, "user": dict(row) if row else None}


@router.post("/password")
def change_password(
    body: PasswordIn,
    user: dict = Depends(current_user),
    c: sqlite3.Connection = Depends(db_conn),
) -> dict:
    row = c.execute("SELECT pass_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not row or not auth.verify_password(body.old_password, row["pass_hash"]):
        raise HTTPException(status_code=403, detail="原密码不对")
    c.execute(
        "UPDATE users SET pass_hash = ? WHERE id = ?",
        (auth.hash_password(body.new_password), user["id"]),
    )
    c.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    c.commit()
    auth.audit(c, user["username"], "password_changed")
    return {"ok": True, "note": "所有会话已失效,请重新登录"}


@router.post("/register")
def register(body: LoginIn, c: sqlite3.Connection = Depends(db_conn)) -> dict:
    """自助注册:只能建 role=user 的普通账号,不给管理员。
    只在本地测试库里开,生产 core 没这个接口。"""
    name = body.username.strip()
    # pydantic 的 min_length 校验的是原始串,纯空格 "  " 长度>1 能过;
    # strip 后必须再判空,否则会写进一个永远无法登录的空账号(BUG-01)。
    if not name:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少要 6 位")
    try:
        cur = c.execute(
            "INSERT INTO users(username, pass_hash) VALUES (?,?)",
            (name, auth.hash_password(body.password)),
        )
        c.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="用户名已存在")
    auth.audit(c, name, "user_registered")
    return {"ok": True, "user": {"id": cur.lastrowid, "username": name, "role": "user"}}


# ---------- 用户管理(仅 admin) ----------

def _require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


@router.get("/users")
def list_users(user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)) -> dict:
    _require_admin(user)
    rows = c.execute(
        "SELECT id, username, role, disabled, created_at, last_login FROM users ORDER BY id"
    ).fetchall()
    return {"ok": True, "users": db_rows(rows)}


@router.post("/users")
def create_user(
    body: UserIn, user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)
) -> dict:
    _require_admin(user)
    # 与 BUG-01 同源:strip 后判空,避免 admin 建出无法登录的空名账号
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="用户名不能为空")
    try:
        cur = c.execute(
            "INSERT INTO users(username, pass_hash, role) VALUES (?,?,?)",
            (body.username.strip(), auth.hash_password(body.password), body.role),
        )
        c.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="用户名已存在")
    auth.audit(c, user["username"], "user_created", body.username.strip())
    return {"ok": True, "id": cur.lastrowid}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int, user: dict = Depends(current_user), c: sqlite3.Connection = Depends(db_conn)
) -> dict:
    _require_admin(user)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="不能删自己")
    c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    cur = c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    c.commit()
    auth.audit(c, user["username"], "user_deleted", str(user_id))
    return {"ok": True, "deleted": cur.rowcount}


def db_rows(rows) -> list:
    import db as _db

    return _db.rows_to_list(rows)
