# -*- coding: utf-8 -*-
"""若的工作区 · 存储层(SQLite):会话 / 消息 / 任务。

纯标准库,和 Core、记忆库一样用 SQLite,但**独立库**,不混表。
HTTP 服务是多线程的,所以连接开 `check_same_thread=False`,所有读写过一把锁。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL DEFAULT 'local',
  title       TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id          TEXT PRIMARY KEY,
  conv_id     TEXT NOT NULL,
  role        TEXT NOT NULL,              -- user / assistant / tool / system
  content     TEXT NOT NULL DEFAULT '',
  meta_json   TEXT NOT NULL DEFAULT '{}',
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msg_conv ON messages(conv_id, created_at);
CREATE TABLE IF NOT EXISTS tasks (
  id          TEXT PRIMARY KEY,
  conv_id     TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'todo',   -- todo / doing / done / failed
  result      TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  done_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_task_status ON tasks(status, created_at);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _id(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:20]


class Store:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.lock = threading.RLock()
        self.c = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        with self.lock:
            self.c.execute("PRAGMA journal_mode=WAL")
            self.c.executescript(SCHEMA)
            self.c.commit()

    # ---------------- 内部 ----------------
    def _x(self, sql: str, params: tuple):
        with self.lock:
            cur = self.c.execute(sql, params)
            self.c.commit()
            return cur

    def _q(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.c.execute(sql, params).fetchall()]

    # ---------------- 会话 ----------------
    def create_conversation(self, title: str = "", user_id: str = "local") -> dict:
        cid, now = _id("conv"), _now()
        self._x("INSERT INTO conversations(id,user_id,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                (cid, user_id, title, now, now))
        return {"id": cid, "title": title, "user_id": user_id, "created_at": now}

    def conversations(self, limit: int = 20) -> list[dict]:
        return self._q("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,))

    def touch(self, cid: str) -> None:
        self._x("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), cid))

    # ---------------- 消息 ----------------
    def add_message(self, conv_id: str, role: str, content: str, meta: dict | None = None) -> dict:
        mid, now = _id("msg"), _now()
        self._x("INSERT INTO messages(id,conv_id,role,content,meta_json,created_at) VALUES (?,?,?,?,?,?)",
                (mid, conv_id, role, str(content), json.dumps(meta or {}, ensure_ascii=False), now))
        self._x("UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id))
        return {"id": mid, "role": role, "content": str(content), "created_at": now}

    def messages(self, conv_id: str, limit: int = 50) -> list[dict]:
        rows = self._q("SELECT * FROM messages WHERE conv_id=? ORDER BY created_at ASC LIMIT ?",
                       (conv_id, limit))
        for d in rows:
            try:
                d["meta"] = json.loads(d.pop("meta_json") or "{}")
            except Exception:
                d["meta"] = {}
        return rows

    # ---------------- 任务 ----------------
    def add_task(self, title: str, conv_id: str = "") -> dict:
        tid = _id("task")
        self._x("INSERT INTO tasks(id,conv_id,title,status,created_at) VALUES (?,?,?,'todo',?)",
                (tid, conv_id, title, _now()))
        return {"id": tid, "title": title, "status": "todo"}

    def finish_task(self, tid: str, ok: bool = True, result: str = "") -> dict:
        status = "done" if ok else "failed"
        self._x("UPDATE tasks SET status=?, result=?, done_at=? WHERE id=?",
                (status, result, _now(), tid))
        return {"ok": True, "id": tid, "status": status}

    def tasks(self, status: str | None = None, limit: int = 30) -> list[dict]:
        if status:
            return self._q("SELECT * FROM tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                           (status, limit))
        return self._q("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))

    def close(self) -> None:
        with self.lock:
            self.c.close()
