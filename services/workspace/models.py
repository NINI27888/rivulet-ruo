# -*- coding: utf-8 -*-
"""工作区 · 模型管理(密钥只写不读回)。

存哪:工作区自己的 SQLite(独立库)。密钥落库是明文,但**任何查询接口都不返回它**,
只返回 `has_key: true/false`;以后接 Windows DPAPI 再加密。

支持的 provider:deepseek / openai / ollama / custom(兼容 OpenAI 协议)。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid

PROVIDERS = ("deepseek", "openai", "ollama", "custom")

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  provider   TEXT NOT NULL,
  base_url   TEXT NOT NULL DEFAULT '',
  model      TEXT NOT NULL DEFAULT '',
  api_key    TEXT NOT NULL DEFAULT '',
  enabled    INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _mask(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k != "api_key"}
    out["has_key"] = bool(row.get("api_key"))
    out["enabled"] = bool(row.get("enabled"))
    return out


class ModelStore:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.lock = threading.RLock()
        self.c = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        with self.lock:
            self.c.executescript(SCHEMA)
            self.c.commit()

    def add(self, *, name: str, provider: str, base_url: str = "", model: str = "",
            api_key: str = "", enabled: bool = False) -> dict:
        provider = str(provider or "").lower()
        if provider not in PROVIDERS:
            raise ValueError("provider 只能是 %s" % "/".join(PROVIDERS))
        if not str(name or "").strip():
            raise ValueError("name 不能为空")
        mid = "mdl_" + uuid.uuid4().hex[:16]
        with self.lock:
            self.c.execute("INSERT INTO models(id,name,provider,base_url,model,api_key,enabled,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?)",
                           (mid, name.strip(), provider, base_url, model, api_key,
                            1 if enabled else 0, _now()))
            self.c.commit()
        return self.get(mid)

    def list(self) -> list[dict]:
        with self.lock:
            rows = [dict(r) for r in self.c.execute(
                "SELECT * FROM models ORDER BY created_at DESC,rowid DESC").fetchall()]
        return [_mask(r) for r in rows]

    def active(self) -> dict | None:
        """Internal only: the selected model with its credential."""
        with self.lock:
            row = self.c.execute(
                "SELECT * FROM models WHERE enabled=1 ORDER BY created_at DESC,rowid DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def activate(self, settings: dict) -> dict:
        """Save a tested configuration and switch the active model in one transaction."""
        provider, base, model, key = (settings[k] for k in ("provider", "base_url", "model", "api_key"))
        with self.lock, self.c:
            row = self.c.execute(
                "SELECT id FROM models WHERE provider=? AND base_url=? AND model=? "
                "ORDER BY created_at DESC,rowid DESC LIMIT 1", (provider, base, model)
            ).fetchone()
            self.c.execute("UPDATE models SET enabled=0 WHERE enabled=1")
            if row:
                mid = row["id"]
                self.c.execute("UPDATE models SET api_key=?,enabled=1 WHERE id=?", (key, mid))
            else:
                mid = "mdl_" + uuid.uuid4().hex[:16]
                self.c.execute(
                    "INSERT INTO models(id,name,provider,base_url,model,api_key,enabled,created_at) "
                    "VALUES (?,?,?,?,?,?,1,?)", (mid, model, provider, base, model, key, _now())
                )
            return self.get(mid)

    def get(self, mid: str) -> dict | None:
        with self.lock:
            row = self.c.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()
        return _mask(dict(row)) if row else None

    def get_full(self, mid: str) -> dict | None:
        """仅内部/Provider 用,带明文 key;不要塞进 HTTP 响应。"""
        with self.lock:
            row = self.c.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()
        return dict(row) if row else None

    def set_enabled(self, mid: str, enabled: bool) -> dict:
        with self.lock, self.c:
            cur = self.c.execute("UPDATE models SET enabled=? WHERE id=?",
                                 (1 if enabled else 0, mid))
            found = cur.rowcount == 1
            if enabled and found:
                self.c.execute("UPDATE models SET enabled=0 WHERE id!=? AND enabled=1", (mid,))
        return {"ok": found, "id": mid, "enabled": bool(enabled)}

    def delete(self, mid: str) -> dict:
        with self.lock:
            cur = self.c.execute("DELETE FROM models WHERE id=?", (mid,))
            self.c.commit()
        return {"ok": cur.rowcount == 1}

    def close(self) -> None:
        with self.lock:
            self.c.close()
