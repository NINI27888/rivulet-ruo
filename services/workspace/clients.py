# -*- coding: utf-8 -*-
"""工作区用的小客户端:小窝、状态。纯标准库,超时短,挂了不拖累对话。"""
from __future__ import annotations

import json
import urllib.request


class _Base:
    def __init__(self, base: str, timeout: float = 2.0):
        self.base = str(base).rstrip("/")
        self.timeout = float(timeout)

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))


class HomeClient(_Base):
    def status(self) -> dict:
        js = self._get("/status")
        return {"phase": js.get("phase"), "coins": js.get("coins"),
                "tasks_todo": js.get("tasks_todo"),
                "characters": [c.get("name") for c in (js.get("characters") or [])]}


class StatusClient(_Base):
    def overview(self) -> dict:
        js = self._get("/api/overview")
        return {"summary": js.get("summary"), "up": js.get("up"), "total": js.get("total"),
                "modules": [{"name": m.get("name"), "ok": m.get("ok")}
                            for m in (js.get("modules") or [])]}
