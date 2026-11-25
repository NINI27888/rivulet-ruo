# -*- coding: utf-8 -*-
"""统一记忆库的本地 HTTP API(默认 127.0.0.1:18091)。

只绑本机;可选 token(环境变量 RIVULET_MEMORY_TOKEN),设了就必须带 X-Memory-Token。
纯标准库,方便以后塞进 EXE 一起起。

跑:
    python api.py                 # 18091
    python api.py --port 18092
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from memory.engine import MemoryEngine  # noqa: E402

MAX_BODY = 1024 * 1024
TOKEN = os.environ.get("RIVULET_MEMORY_TOKEN", "")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def default_db_path() -> str:
    current = os.path.join(os.path.dirname(HERE), "data", "memory", "memory.db")
    legacy = os.path.join(HERE, "data", "memory.db")
    # Older portable builds used a nested data folder. Keep using its database
    # when the user already has one there instead of creating an empty library.
    return legacy if os.path.isfile(legacy) and not os.path.isfile(current) else current


class Handler(BaseHTTPRequestHandler):
    server_version = "RivuletMemory/0.1"
    db_path = default_db_path()

    def _origin(self):
        origin = self.headers.get("Origin")
        if origin is not None:
            try:
                parts = urllib.parse.urlsplit(origin)
                if (parts.scheme in ("http", "https")
                        and parts.hostname in LOOPBACK | {"tauri.localhost"}
                        and parts.username is None and not (parts.path or parts.query or parts.fragment)):
                    return origin
            except ValueError:
                pass
        return None

    def _cors_headers(self):
        origin = self._origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Memory-Token")
            self.send_header("Vary", "Origin")

    # ---------------- 工具 ----------------
    def _send(self, code: int, obj) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        if n <= 0:
            return {}
        data = json.loads(self.rfile.read(n).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("body must be object")
        return data

    def _local(self) -> bool:
        try:
            host = urllib.parse.urlsplit("//" + self.headers.get("Host", "")).hostname
        except ValueError:
            return False
        return host in LOOPBACK and (
            self.headers.get("Origin") is None or self._origin() is not None
        )

    def _authorize(self) -> bool:
        if not self._local():
            self._send(403, {"ok": False, "error": "local access only"})
            return False
        if TOKEN and not hmac.compare_digest(
            self.headers.get("X-Memory-Token", "").encode("utf-8"), TOKEN.encode("utf-8")
        ):
            self._send(401, {"ok": False, "error": "token required"})
            return False
        return True

    def _engine(self) -> MemoryEngine:
        return MemoryEngine(self.db_path)

    def log_message(self, fmt, *args):        # 不往 stderr 刷每条请求
        pass

    # ---------------- 路由 ----------------
    def do_OPTIONS(self):
        if not self._local():
            return self._send(403, {"ok": False, "error": "local access only"})
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._authorize():
            return
        u = urllib.parse.urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        qs = urllib.parse.parse_qs(u.query)
        m = self._engine()
        try:
            if parts == ["health"]:
                return self._send(200, {"ok": True, "db": self.db_path})
            if parts == ["api", "memory", "stats"]:
                return self._send(200, {"ok": True, **m.stats(user_id=qs.get("user_id", [None])[0])})
            if parts == ["api", "memory", "search"]:
                res = m.search(
                    (qs.get("q") or [""])[0],
                    user_id=(qs.get("user_id") or [None])[0],
                    sources=[s for s in (qs.get("source") or []) if s],
                    kinds=[k for k in (qs.get("kind") or []) if k],
                    permission=(qs.get("permission") or [None])[0],
                    since=(qs.get("since") or [None])[0],
                    until=(qs.get("until") or [None])[0],
                    limit=int((qs.get("limit") or ["20"])[0]),
                    offset=int((qs.get("offset") or ["0"])[0]),
                )
                return self._send(200, {"ok": True, **res})
            if len(parts) == 3 and parts[:2] == ["api", "memory"]:
                item = m.get(parts[2])
                if not item:
                    return self._send(404, {"ok": False, "error": "not found"})
                return self._send(200, {"ok": True, "item": item})
            return self._send(404, {"ok": False, "error": "no such route"})
        except ValueError as exc:
            return self._send(400, {"ok": False, "error": str(exc)})
        finally:
            m.close()

    def do_POST(self):
        if not self._authorize():
            return
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
        if parts != ["api", "memory"]:
            return self._send(404, {"ok": False, "error": "no such route"})
        try:
            body = self._body()
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})
        m = self._engine()
        try:
            res = m.add(
                content=body.get("content", ""),
                source=body.get("source", "manual"),
                user_id=body.get("user_id", "local"),
                kind=body.get("kind", "fact"),
                occurred_at=body.get("occurred_at"),
                permission=body.get("permission", "private"),
                scope=body.get("scope", ""),
                source_ref=body.get("source_ref", ""),
                context=body.get("context") or {},
            )
            return self._send(200, res)
        except ValueError as e:
            return self._send(422, {"ok": False, "error": str(e)})
        finally:
            m.close()

    def do_PATCH(self):
        if not self._authorize():
            return
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
        if len(parts) != 3 or parts[:2] != ["api", "memory"]:
            return self._send(404, {"ok": False, "error": "no such route"})
        try:
            body = self._body()
        except Exception as e:
            return self._send(400, {"ok": False, "error": str(e)})
        m = self._engine()
        try:
            res = m.update(parts[2], **body)
            return self._send(200 if res.get("ok") else 404, res)
        except ValueError as e:
            return self._send(422, {"ok": False, "error": str(e)})
        finally:
            m.close()

    def do_DELETE(self):
        if not self._authorize():
            return
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
        if len(parts) != 3 or parts[:2] != ["api", "memory"]:
            return self._send(404, {"ok": False, "error": "no such route"})
        m = self._engine()
        try:
            res = m.delete(parts[2])
            return self._send(200 if res.get("ok") else 404, res)
        finally:
            m.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="memory-api")
    ap.add_argument("--port", type=int, default=int(os.environ.get("RIVULET_MEMORY_PORT") or 18091))
    ap.add_argument("--db", default=os.environ.get("RIVULET_MEMORY_DB")
                    or default_db_path())
    a = ap.parse_args(argv)
    Handler.db_path = os.path.abspath(a.db)
    os.makedirs(os.path.dirname(Handler.db_path) or ".", exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"[memory-api] http://127.0.0.1:{a.port} db={Handler.db_path}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
