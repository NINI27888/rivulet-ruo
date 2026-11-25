from contextlib import closing
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
from memory import api  # noqa: E402
from memory.engine import MemoryEngine  # noqa: E402


class MemoryServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "memory.db")
        handler = type("TestHandler", (api.Handler,), {"db_path": self.db})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.addCleanup(self.server.server_close)
        self.worker = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 3)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)) as connection:
            connection.request(method, path, None if body is None else json.dumps(body),
                               {"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.headers), json.loads(payload) if payload else None

    def test_store_search_pin_and_reopen_existing_database(self):
        path = "/api/memory"
        with patch.object(api, "TOKEN", ""):
            sample = {"content": "今天给小鲸鱼换了水", "source": "home", "source_ref": "test-event"}
            status, _, written = self.request("POST", path, sample)
            self.assertEqual(status, 200)
            self.assertTrue(written["ok"])
            detail = f"{path}/{written['id']}"
            self.assertTrue(self.request("POST", path, sample)[2]["duplicate"])

            for fragment in ("鲸鱼", "给小鲸鱼"):
                response = self.request("GET", path + "/search?q=" + quote(fragment) + "&source=home")[2]
                self.assertEqual([r["id"] for r in response["items"]], [written["id"]])
            self.assertEqual(self.request("GET", path + "/search?source=qq")[2]["total"], 0)
            self.assertTrue(self.request("PATCH", detail, {"context": {"pinned": True}})[2]["ok"])
            self.assertTrue(self.request("GET", detail)[2]["item"]["pinned"])
            self.assertEqual(self.request("GET", path + "/stats")[2]["pinned"], 1)

            reopened = MemoryEngine(self.db)
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.get(written["id"])["content"], sample["content"])
            self.assertTrue(reopened.get(written["id"])["context"]["pinned"])
            self.assertTrue(self.request("DELETE", detail)[2]["ok"])
            self.assertEqual(self.request("GET", detail)[0], 404)

    def test_local_browser_access_and_optional_token(self):
        path = "/api/memory/search"
        with patch.object(api, "TOKEN", "fixture"):
            self.assertEqual(self.request("GET", path)[0], 401)
            credential = {"X-Memory-Token": "fixture"}
            status, headers, body = self.request("GET", path, headers=credential)
            self.assertEqual((status, body["total"]), (200, 0))
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertNotIn("Access-Control-Allow-Origin", headers)
            status, _headers, _body = self.request("GET", path, headers={
                **credential, "Origin": "https://example.invalid",
            })
            self.assertEqual(status, 403)
            self.assertEqual(self.request("GET", path, headers={
                **credential, "Host": "example.invalid",
            })[0], 403)
            origin = "http://localhost:64000"
            status, headers, _body = self.request("OPTIONS", path, headers={"Origin": origin})
            self.assertEqual(status, 204)
            self.assertEqual(headers["Access-Control-Allow-Origin"], origin)
            self.assertIn("PATCH", headers["Access-Control-Allow-Methods"])

    def test_invalid_query_and_legacy_storage_path(self):
        with patch.object(api, "TOKEN", ""):
            self.assertEqual(self.request("GET", "/api/memory/search?limit=bad")[0], 400)
            self.assertEqual(self.request("POST", "/api/memory", {
                "content": "sample", "context": [True],
            })[0], 422)
        source = Path(self.tmp.name) / "services" / "memory"
        legacy = source / "data" / "memory.db"
        current = source.parent / "data" / "memory" / "memory.db"
        with patch.object(api, "HERE", str(source)):
            self.assertEqual(Path(api.default_db_path()), current)
            legacy.parent.mkdir(parents=True)
            legacy.touch()
            self.assertEqual(Path(api.default_db_path()), legacy)
            current.parent.mkdir(parents=True)
            current.touch()
            self.assertEqual(Path(api.default_db_path()), current)


if __name__ == "__main__":
    unittest.main()
