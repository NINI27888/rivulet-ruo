import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


@contextmanager
def service_fixture():
    with tempfile.TemporaryDirectory(prefix="rivulet-bili-test-") as tmp:
        folder = Path(tmp)
        for relative in [
            "services/bili_service.py", "fatfish/bili/client.py", "fatfish/bili/manage.py",
            "fatfish/bili/publish.py", "fatfish/bili/uploads.py",
        ]:
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        (folder / "fatfish/bili/upload.py").write_text(
            """
from pathlib import Path
import time

class Uploader:
    def __init__(self, account):
        assert account.cookie.get("SESSDATA") == "fixture-session"

    def upload(self, video, progress):
        file = Path(video)
        gate = file.with_suffix(".continue")
        deadline = time.monotonic() + 8
        while not gate.exists():
            assert time.monotonic() < deadline
            time.sleep(.05)
        content = file.read_bytes()
        assert content == b"fixture video"
        progress(len(content), len(content))
        return {"filename": "fixture-upload", "size": len(content)}

    def submit(self, *args):
        raise AssertionError("Dry run attempted to submit.")
""",
            encoding="utf-8",
        )
        cookie = folder / "previous-location.json"
        cookie.write_text(json.dumps({"SESSDATA": "fixture-session", "bili_jct": "fixture-csrf"}),
                          encoding="utf-8")
        spec = importlib.util.spec_from_file_location("test_service", folder / "services/bili_service.py")
        service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(service)
        service.CFG["cookie_file"] = str(cookie)
        with ThreadingHTTPServer(("127.0.0.1", 0), service.Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def request(endpoint, body=None):
                if body is None:
                    req = urllib.request.Request(base + endpoint)
                else:
                    req = urllib.request.Request(base + endpoint, data=json.dumps(body).encode("utf-8"),
                                                 headers={"Content-Type": "application/json"})
                with direct.open(req, timeout=10) as response:
                    return json.load(response), response.headers

            try:
                yield folder, service, request
            finally:
                server.shutdown()
                thread.join(2)


def completed(request, endpoint):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        state = request(endpoint)[0]
        if not state["running"]:
            return state
        time.sleep(.05)
    raise AssertionError(f"Background work did not finish: {endpoint}")


def exercise():
    with service_fixture() as (folder, service, request):
        from client import Bili
        import manage

        assert request("/api/reply", {"bvid": "BVfixture", "text": "待发送"})[0]["ok"] is False
        item = {"bvid": "BVfixture", "aid": 5, "title": "测试稿件"}
        comments = [{"member": {"uname": "测试账号"}, "content": {"message": "测试评论"}}]
        with (patch.object(Bili, "nav", return_value={"isLogin": True, "mid": 1, "uname": "测试账号"}),
              patch.object(Bili, "get_wbi", return_value={"list": {"vlist": [item]}}) as videos,
              patch.object(Bili, "aid_of", return_value=5),
              patch.object(Bili, "comments", side_effect=[[], comments]) as read_comments,
              patch.object(Bili, "reply", side_effect=[(False, "评论被拒绝"), (True, "已发送")]),
              patch.object(manage, "edit", side_effect=[{"code": -101, "message": "稿件修改被拒绝"}, {"code": 0}])):
            assert request("/api/status")[0]["logged_in"] is True
            assert request("/api/config/write", {"allow_write": True})[0]["allow_write"] is True
            assert request("/api/videos")[0]["items"][0]["title"] == item["title"]
            assert request("/api/comments?bvid=BVfixture")[0]["items"] == []
            body = {"bvid": "BVfixture", "text": "测试回复"}
            assert request("/api/reply", body)[0] == {"ok": False, "error": "评论被拒绝"}
            assert request("/api/reply", body)[0]["ok"] is True
            assert request("/api/comments?bvid=BVfixture")[0]["items"][0]["text"] == "测试评论"
            assert read_comments.call_count == 2
            edit = {"bvid": "BVfixture", "title": "改标题"}
            assert request("/api/edit", edit)[0] == {"ok": False, "error": "稿件修改被拒绝"}
            assert request("/api/edit", edit)[0]["ok"] is True
            assert request("/api/videos")[0]["ok"] is True
            assert videos.call_count == 2
        print("PASS login status, draft and comment data, accepted/refused writes and cache refresh")

        video = folder / "样例.mp4"
        video.write_bytes(b"fixture video")
        body = {"file": str(video), "title": "上传冒烟", "dry": True}
        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = list(pool.map(lambda _: request("/api/publish", body)[0]["ok"], range(2)))
        assert accepted.count(True) == 1
        video.with_suffix(".continue").touch()
        publication = completed(request, "/api/publish_status")
        assert publication["ok_result"] is True, publication["log"]
        assert publication["dry"] and publication["pct"] == 100
        assert request("/api/uploads")[0]["pending"] == 1
        print("PASS concurrent upload guard, actual dry-upload child and pending ledger")

        cover = folder / "封面.png"
        cover.write_bytes(PNG)
        def cover_result(path):
            return request("/api/cover?" + urllib.parse.urlencode({"path": str(path)}))

        image, headers = cover_result(cover)
        assert image["ok"] and image["mime"] == "png" and base64.b64decode(image["b64"]) == PNG
        assert "Access-Control-Allow-Origin" not in headers
        invalid = folder / "无效.png"
        invalid.write_text("fixture")
        assert cover_result(invalid)[0]["ok"] is False
        invalid = cover.rename(folder / "无效.txt")
        assert cover_result(invalid)[0]["ok"] is False
        with cover.open("wb") as stream:
            stream.truncate(6 * 1024 * 1024 + 1)
        assert cover_result(cover)[0]["ok"] is False
        print("PASS selected cover preview and format, size, and response checks")

        assert request("/api/daily/watch", {"count": "无效"})[0]["ok"] is False
        assert request("/api/daily/status")[0]["running"] is False
        with (patch.object(Bili, "rcmd", return_value=[{"bvid": "BVfixture", "title": "显卡测试"}]),
              patch.object(Bili, "view", return_value={"desc": ""}),
              patch.object(Bili, "like", return_value=(False, "点赞被拒绝")),
              patch.object(Bili, "reply", return_value=(False, "评论被拒绝")),
              patch.object(service, "_gen_comment", return_value="测试评论")):
            assert request("/api/daily/watch", {"count": 1, "auto_like": True, "auto_comment": True})[0]["ok"]
            recommendations = completed(request, "/api/daily/status")
            result = recommendations["results"][0]
            assert result["liked"] is False and result["commented"] is False
            assert any("点赞被拒绝" in line for line in recommendations["log"])
            assert any("评论被拒绝" in line for line in recommendations["log"])
        print("PASS recommendations start guard and upstream rejection reporting")


if __name__ == "__main__":
    exercise()
