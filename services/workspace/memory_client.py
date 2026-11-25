# -*- coding: utf-8 -*-
"""统一记忆库的客户端(Adapter / 工作区侧)。纯标准库,同步调用。

用法::

    mem = MemoryClient("http://127.0.0.1:18091")
    mem.add(content="...", source="qq", source_ref="qq:10001:9001")
    mem.search("番茄", source=["farm"], since="2026-11-05 00:00")
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class MemoryError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"memory {status}: {detail}")
        self.status = status
        self.detail = detail


class MemoryClient:
    def __init__(self, base: str = "http://127.0.0.1:18091", token: str = "", timeout: float = 5.0):
        self.base = str(base).rstrip("/")
        self.token = str(token or "")
        self.timeout = float(timeout)

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.token:
            headers["X-Memory-Token"] = self.token
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise MemoryError(e.code, e.read().decode("utf-8", "replace")[:200]) from None
        except Exception as e:
            raise MemoryError(0, f"连不上记忆服务: {e}") from None

    def add(self, *, content: str, source: str = "manual", user_id: str = "local",
            kind: str = "fact", occurred_at: str | None = None,
            permission: str = "private", source_ref: str = "",
            context: dict | None = None) -> dict:
        return self._req("POST", "/api/memory", {
            "content": content, "source": source, "user_id": user_id, "kind": kind,
            "occurred_at": occurred_at, "permission": permission,
            "source_ref": source_ref, "context": context or {},
        })

    def search(self, q: str = "", *, sources=None, kinds=None, user_id: str | None = None,
               since: str | None = None, until: str | None = None, limit: int = 20) -> dict:
        qs = [("q", q), ("limit", str(limit))]
        for s in (sources or []):
            qs.append(("source", s))
        for k in (kinds or []):
            qs.append(("kind", k))
        if user_id:
            qs.append(("user_id", user_id))
        if since:
            qs.append(("since", since))
        if until:
            qs.append(("until", until))
        return self._req("GET", "/api/memory/search?" + urllib.parse.urlencode(qs))

    def stats(self) -> dict:
        return self._req("GET", "/api/memory/stats")
