# -*- coding: utf-8 -*-
"""SQLite 连接、迁移执行与状态查询。只碰 config 里指定的那个库。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
MIGRATIONS = BASE / "migrations"

_DB_PATH = str(BASE.parent / "data" / "fatfish.db")


def set_path(path: str) -> None:
    global _DB_PATH
    _DB_PATH = path


def get_path() -> str:
    return _DB_PATH


def connect(db_path: str) -> sqlite3.Connection:
    # check_same_thread=False:FastAPI 的同步端点在任意线程池线程里跑,
    # 连接由依赖注入创建后可能换线程使用。每个请求各自新建、互不共享,
    # 所以放开线程检查是安全的(SQLite 仍按串行写锁保证一致性)。
    conn = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def conn() -> sqlite3.Connection:
    """给一次请求用的连接(调用方负责 close)。"""
    return connect(_DB_PATH)


def init(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    c = connect(db_path)
    c.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    c.commit()
    applied = {r["name"] for r in c.execute("SELECT name FROM schema_migrations")}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name in applied:
            continue
        c.executescript(path.read_text(encoding="utf-8"))
        c.execute("INSERT INTO schema_migrations(name) VALUES (?)", (path.name,))
        c.commit()
        print("[fatfish-core] migration applied: " + path.name, flush=True)
    c.close()


def status(db_path: str) -> dict:
    try:
        c = connect(db_path)
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        counts = {}
        for name in ("users", "nodes", "commands", "sessions"):
            try:
                counts[name] = c.execute("SELECT COUNT(*) AS n FROM %s" % name).fetchone()["n"]
            except Exception:
                counts[name] = None
        c.close()
        return {"ok": True, "path": db_path, "tables": [r["name"] for r in rows], "counts": counts}
    except Exception as exc:
        return {"ok": False, "path": db_path, "error": repr(exc)}


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]
