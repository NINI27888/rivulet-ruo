# -*- coding: utf-8 -*-
"""若的工作区 · 本地服务(127.0.0.1:18095)。

    GET  /health
    POST /api/chat                 {"text": "...", "conversation_id": "可选"}
    GET  /api/conversations
    GET  /api/conversations/{id}   返回消息列表
    GET  /api/tasks?status=

依赖(都可缺省,缺了就少一个工具):记忆 18091 / 小窝 18094 / 状态 18090。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
AI = os.path.dirname(HERE)
sys.path.insert(0, AI)

from workspace.chat import ChatService                  # noqa: E402
from workspace.clients import HomeClient, StatusClient  # noqa: E402
from workspace.memory_client import MemoryClient        # noqa: E402
from workspace.models import ModelStore                 # noqa: E402
from workspace.provider_llm import (LLMProvider, ModelError, complete_message,  # noqa: E402
                                    model_settings, probe_model)
from workspace.store import Store                       # noqa: E402
from workspace.tools import (HomeStatusTool, MemorySearchTool,  # noqa: E402
                             Registry, StatusTool)


class Handler(BaseHTTPRequestHandler):
    server_version = "RivuletWorkspace/0.1"
    chat: ChatService = None
    store: Store = None
    models: ModelStore = None

    def _send(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        data = json.loads(self.rfile.read(n).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("body must be object")
        return data

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        qs = urllib.parse.parse_qs(u.query)
        if parts in (["health"], ["api", "health"]):
            return self._send(200, {"ok": True, "service": "rivulet-workspace"})
        if parts == ["api", "conversations"]:
            return self._send(200, {"ok": True, "items": self.store.conversations()})
        if len(parts) == 3 and parts[:2] == ["api", "conversations"]:
            return self._send(200, {"ok": True, "id": parts[2],
                                    "messages": self.store.messages(parts[2])})
        if parts == ["api", "tasks"]:
            st = (qs.get("status") or [None])[0]
            return self._send(200, {"ok": True, "items": self.store.tasks(st)})
        if parts == ["api", "models"]:
            return self._send(200, {"ok": True, "items": self.models.list()})
        if parts == ["api", "models", "status"]:
            try:
                st = self.chat.provider.status()
            except Exception as e:
                st = {"ok": False, "error": str(e)[:200]}
            return self._send(200, st)
        return self._send(404, {"ok": False, "error": "no such route"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/models":
            return self._add_model()
        if path == "/api/models/configure":
            return self._configure_model()
        if path == "/api/models/test":
            try:
                model = self.chat.provider.require_model()
                probe_model(model)
                return self._send(200, {"ok": True, "model": model["model"]})
            except ModelError as e:
                return self._send(200, {"ok": False, "error": str(e)})
        if path == "/api/models/respond":
            return self._respond_as_configured()
        if path == "/api/persona/changed":
            try:
                self.chat.reset_context()
            except Exception:
                pass
            return self._send(200, {"ok": True, "hint": "上下文已清空,下一句话开新会话"})
        if path == "/api/generate":
            try:
                body = self._body()
            except Exception as e:
                return self._send(400, {"ok": False, "error": str(e)})
            prompt = str(body.get("prompt") or "").strip()
            if not prompt:
                return self._send(422, {"ok": False, "error": "prompt required"})
            system = str(body.get("system") or "").strip()
            msgs = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompt}]
            try:
                text = complete_message(self.chat.provider.require_model(), msgs)["content"]
            except ModelError as e:
                return self._send(200, {"ok": False, "error": str(e)})
            return self._send(200, {"ok": True, "text": text})
        if path != "/api/chat":
            return self._send(404, {"ok": False, "error": "no such route"})
        try:
            body = self._body()
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})
        text = str(body.get("text") or "").strip()
        if not text:
            return self._send(422, {"ok": False, "error": "text required"})
        try:
            self.chat.provider.require_model()
        except ModelError as e:
            return self._send(200, {"ok": False, "error": str(e)})
        out = self.chat.send(text, body.get("conversation_id"))
        return self._send(200, out)

    def _configure_model(self):
        try:
            body = self._body()
            model_id = body.get("existing_id")
            existing = self.models.get_full(model_id) if isinstance(model_id, str) else None
            settings = model_settings(body, existing)
            probe_model(settings)
            item = self.models.activate(settings)
        except (ModelError, ValueError) as e:
            return self._send(200, {"ok": False, "error": str(e)})
        except Exception:
            return self._send(200, {"ok": False, "error": "保存失败。请检查程序所在文件夹是否可以写入。"})
        return self._send(200, {"ok": True, "item": item})

    def _respond_as_configured(self):
        try:
            body = self._body()
            messages = body.get("messages")
            if not isinstance(messages, list) or not 1 <= len(messages) <= 60 or any(
                not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant")
                or not isinstance(m.get("content"), str) for m in messages
            ):
                raise ModelError("缺少可发送给模型的消息。")
            tokens = min(2048, max(32, int(body.get("max_tokens") or 220)))
            temperature = min(2, max(0, float(body.get("temperature", 0.8))))
            model = self.chat.provider.require_model()
            session_id = body.get("session_id")
            text = complete_message(model, messages, max_tokens=tokens, temperature=temperature,
                                    session_id=session_id if isinstance(session_id, str) else None)["content"]
            return self._send(200, {"ok": True, "text": text})
        except (ModelError, ValueError, TypeError) as e:
            return self._send(200, {"ok": False, "error": str(e)})

    def _add_model(self):
        try:
            body = self._body()
            item = self.models.add(name=body.get("name", ""),
                                   provider=body.get("provider", ""),
                                   base_url=body.get("base_url", ""),
                                   model=body.get("model", ""),
                                   api_key=body.get("api_key", ""),
                                   enabled=bool(body.get("enabled", False)))
        except ValueError as e:
            return self._send(422, {"ok": False, "error": str(e)})
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})
        return self._send(200, {"ok": True, "item": item})

    def do_DELETE(self):
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
        if len(parts) != 3 or parts[:2] != ["api", "models"]:
            return self._send(404, {"ok": False, "error": "no such route"})
        res = self.models.delete(parts[2])
        return self._send(200 if res.get("ok") else 404, res)

    def do_PATCH(self):
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
        if len(parts) != 3 or parts[:2] != ["api", "models"]:
            return self._send(404, {"ok": False, "error": "no such route"})
        try:
            body = self._body()
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})
        res = self.models.set_enabled(parts[2], bool(body.get("enabled", False)))
        return self._send(200 if res.get("ok") else 404, res)


def build(store_path: str, memory_base: str, home_base: str, status_base: str) -> Handler:
    store = Store(store_path)
    reg = Registry()
    if memory_base:
        reg.register(MemorySearchTool(MemoryClient(memory_base)))
    if home_base:
        reg.register(HomeStatusTool(HomeClient(home_base)))
    if status_base:
        reg.register(StatusTool(StatusClient(status_base)))
    Handler.store = store
    Handler.models = ModelStore(os.path.join(os.path.dirname(os.path.abspath(store_path)), "models.db"))
    Handler.chat = ChatService(store, reg, provider=LLMProvider(Handler.models))
    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(prog="workspace-api")
    ap.add_argument("--port", type=int, default=int(os.environ.get("RIVULET_WORKSPACE_PORT") or 18095))
    ap.add_argument("--db", default=os.environ.get("RIVULET_WORKSPACE_DB")
                    or os.path.join(AI, "data", "workspace", "workspace.db"))
    ap.add_argument("--memory", default=os.environ.get("RIVULET_MEMORY"))
    ap.add_argument("--home", default=os.environ.get("RIVULET_HOME"))
    ap.add_argument("--status", default=os.environ.get("RIVULET_STATUS"))
    a = ap.parse_args(argv)
    build(a.db, a.memory, a.home, a.status)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"[workspace] http://127.0.0.1:{a.port} db={a.db}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
