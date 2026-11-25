# -*- coding: utf-8 -*-
"""B站 投稿:preupload → UPOS 分片上传(?uploads&output=json)→ 合并 → x/vu/web/add 提交。

只依赖标准库。用法:
    python upload.py probe  <视频路径>          # 只跑 preupload,打印服务端给的 endpoint/biz_id/auth
    python upload.py raw    <视频路径>          # 打印 preupload 的完整返回
    python upload.py upload <视频路径>          # 真上传,拿回 filename(不提交)
    python upload.py submit <视频路径> <json>   # 上传 + 提交

json 字段:title, desc, tag, tid(分区), cover(封面图路径,可空), no_reprint, is_only_self

踩过的坑(2026-10-29 挖到底,见 项目状态.md):
    · 老写法 add/v3 永远回 21015 —— 换成 x/vu/web/add 就好了
    · 申请上传必须 `?uploads&output=json`,不带 output 回 XML 400 InvalidArgument
    · preupload 的 profile 用 ugcupos/bup(不是 ugcfx/bup);合并那步也要 ugcupos/bup
    · 提交时 videos[].filename 只要 upos_uri 的文件名,去掉桶名和扩展名
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import Bili, BiliError, UA  # noqa: E402

PREUPLOAD = "https://member.bilibili.com/preupload"
ADD_V3 = "https://member.bilibili.com/x/vu/web/add/v3"      # 老写法,会回 21015,留着对照
ADD_WEB = "https://member.bilibili.com/x/vu/web/add"          # biliup 用的这个
COVER_UP = "https://member.bilibili.com/x/vu/web/cover/up"


class Uploader:
    def __init__(self, bili=None):
        self.b = bili or Bili()
        self._buvid = None

    # ---------- 基础 ----------

    def buvid(self):
        if self._buvid:
            return self._buvid
        for k in ("buvid3", "buvid4"):
            if self.b.cookie.get(k):
                self._buvid = self.b.cookie[k]
                return self._buvid
        try:
            d = self.b.get("https://api.bilibili.com/x/frontend/finger/spi") or {}
            v = d.get("b_3") or d.get("b_4") or ""
            if v:
                self.b.cookie["buvid3"] = v
                self._buvid = v
                return v
        except BiliError:
            pass
        return ""

    def _req(self, url, data=None, headers=None, method=None, timeout=120):
        """返回 (body_bytes, headers)。"""
        h = {
            "User-Agent": UA,
            "Referer": "https://member.bilibili.com/",
            "Origin": "https://member.bilibili.com",
            "Cookie": "; ".join("%s=%s" % (k, v) for k, v in self.b.cookie.items()),
        }
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            body = e.read()
            raise BiliError("HTTP %s %s: %s" % (
                e.code, url, body[:400].decode("utf-8", "replace")))
        except Exception as e:
            raise BiliError("请求失败 %s (%s)" % (url, e))

    # ---------- preupload ----------

    def preupload(self, path, profile="ugcupos/bup"):
        name = os.path.basename(path)
        size = os.path.getsize(path)
        q = urllib.parse.urlencode({
            "name": name, "size": size, "r": "upos", "profile": profile,
            "ssl": 0, "version": "2.14.0", "build": "2140000",
            "buvid": self.buvid(), "mtime": int(os.path.getmtime(path)),
        })
        body, _ = self._req(PREUPLOAD + "?" + q)
        d = json.loads(body.decode("utf-8", "replace"))
        if d.get("OK") != 1:
            raise BiliError("preupload 失败: %s" % body[:400].decode("utf-8", "replace"))
        return d

    # ---------- 分片上传 ----------

    def _ep(self, pre):
        ep = pre.get("endpoint") or ""
        if ep.startswith("//"):
            ep = "https:" + ep
        elif ep.startswith("http://"):
            ep = "https://" + ep[7:]
        return ep.rstrip("/")

    def _path(self, pre):
        u = pre.get("upos_uri") or ""
        return u[7:] if u.startswith("upos://") else u

    def upload(self, path, chunk_size=None, progress=None):
        """按 upos 的正规流程传:?uploads&output=json 申请 → 分块 PUT → POST 收尾合并。

        这一套是照着 biliup 的实现来的(它一直能用),踩过的坑见 项目状态.md:
          · 申请上传必须带 `output=json`,不然回的是 XML,400 InvalidArgument
          · 收尾那步的 profile 是 `ugcupos/bup`,不是 preupload 里的 ugcfx/bup
          · 收尾要 POST,body 是 {"parts":[{"partNumber":n,"eTag":"etag"},...]}
          · 提交稿件时 filename 只要 upos_uri 的文件名(去掉扩展名、去掉桶名)
        """
        pre = self.preupload(path)
        ep, upath, auth = self._ep(pre), self._path(pre), pre.get("auth") or ""
        biz_id = pre.get("biz_id")
        chunk = int(chunk_size or pre.get("chunk_size") or 10485760)
        total = os.path.getsize(path)
        url = "%s/%s" % (ep, upath)
        hdr = {"X-Upos-Auth": auth}

        # 1) 申请上传,拿 upload_id
        body, _ = self._req(url + "?uploads&output=json", data=b"", method="POST", headers=hdr)
        try:
            upload_id = json.loads(body.decode("utf-8", "replace"))["upload_id"]
        except Exception:
            raise BiliError("申请上传没给 upload_id: %s" % body[:300].decode("utf-8", "replace"))

        # 2) 一块一块 PUT
        chunks = max(1, (total + chunk - 1) // chunk)
        parts = []
        n = 0
        with open(path, "rb") as f:
            for i in range(chunks):
                data = f.read(chunk)
                if not data:
                    break
                q = urllib.parse.urlencode({
                    "uploadId": upload_id, "chunks": chunks, "total": total,
                    "chunk": i, "size": len(data), "partNumber": i + 1,
                    "start": i * chunk, "end": i * chunk + len(data),
                })
                self._req("%s?%s" % (url, q), data=data, method="PUT", timeout=1800,
                          headers=dict(hdr, **{"Content-Type": "application/octet-stream"}))
                parts.append({"partNumber": i + 1, "eTag": "etag"})
                n += len(data)
                if progress:
                    progress(n, total)

        # 3) 收尾合并
        q = urllib.parse.urlencode({
            "name": os.path.basename(path), "uploadId": upload_id, "biz_id": biz_id,
            "output": "json", "profile": "ugcupos/bup",
        })
        last = ""
        for _ in range(5):
            body, _ = self._req("%s?%s" % (url, q), data=json.dumps({"parts": parts}).encode(),
                                method="POST", headers=dict(hdr, **{"Content-Type": "application/json"}),
                                timeout=60)
            last = body.decode("utf-8", "replace")
            try:
                if json.loads(last).get("OK") == 1:
                    return {"filename": os.path.splitext(os.path.basename(upath))[0],
                            "upos_uri": upath, "size": total, "biz_id": biz_id,
                            "upload_id": upload_id}
            except Exception:
                pass
            time.sleep(5)
        raise BiliError("收尾合并没成: %s" % last[:300])

    # ---------- 封面 ----------

    def cover(self, img_path):
        """B站 现在的封面接口要 base64 表单,multipart 会回 -400(2026-11-02 验过)。

        返回 {"code":0,"data":{"url":"https://archive.biliimg.com/..."}},这个 url 直接塞进
        提交稿件的 meta["cover"],就能「投稿时一起带封面」,不用发完再改。
        """
        import base64
        import urllib.parse
        with open(img_path, "rb") as f:
            raw = f.read()
        ext = "jpeg" if img_path.lower().endswith((".jpg", ".jpeg")) else "png"
        data = "data:image/%s;base64,%s" % (ext, base64.b64encode(raw).decode())
        csrf = self.b.cookie.get("bili_jct", "")
        body = urllib.parse.urlencode({"cover": data, "csrf": csrf}).encode()
        r, _ = self._req(COVER_UP, data=body, method="POST",
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        return json.loads(r.decode("utf-8", "replace"))

    # ---------- 提交 ----------

    def submit(self, filename, meta):
        """提交稿件。照 biliup 的写法:x/vu/web/add(不是 add/v3),
        filename 只给 upos_uri 的文件名(去掉扩展名)。"""
        csrf = self.b.cookie.get("bili_jct", "")
        # 提交前先点一下这个,网页端投稿也会先请求它
        try:
            self._req("https://member.bilibili.com/x/geetest/pre/add", timeout=15)
        except BiliError:
            pass
        desc = meta.get("desc", "")
        payload = {
            "copyright": int(meta.get("copyright", 1)),
            "source": meta.get("source", ""),
            "tid": int(meta.get("tid", 188)),
            "cover": meta.get("cover", ""),
            "title": meta["title"][:80],
            "desc_format_id": 0,
            "desc": desc,
            "desc_v2": [{"raw_text": desc, "biz_id": "", "type": 1}],
            "videos": [{"filename": filename, "title": meta["title"][:80], "desc": ""}],
            "dynamic": meta.get("dynamic", ""),
            "interactive": 0,
            "act_reserve_create": 0,
            "no_disturbance": 0,
            "no_reprint": int(meta.get("no_reprint", 1)),
            "subtitle": {"open": 0, "lan": ""},
            "tag": meta.get("tag", ""),
            "dolby": 0,
            "lossless_music": 0,
            "up_selection_reply": False,
            "up_close_reply": False,
            "up_close_danmu": False,
            "web_os": 2,
            "csrf": csrf,
            # 仅自己可见 —— 测试片用;两个字段名都带上,B站 不同版本认的不一样
            "is_only_self": int(meta.get("is_only_self", 0)),
            "no_public": int(meta.get("is_only_self", 0)),
        }
        url = "%s?csrf=%s" % (ADD_WEB, csrf)
        r, _ = self._req(url, data=json.dumps(payload).encode(), method="POST",
                         headers={"Content-Type": "application/json"})
        return json.loads(r.decode("utf-8", "replace"))


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return 2
    mode, path = args[0], args[1]
    u = Uploader()
    if mode == "probe":
        d = u.preupload(path)
        out = {k: d.get(k) for k in ("OK", "endpoint", "upos_uri", "biz_id", "chunk_size", "threads")}
    elif mode == "raw":
        d = u.preupload(path)
        out = {k: v for k, v in d.items() if k != "auth"}
    elif mode == "upload":
        out = u.upload(path, progress=lambda i, n: print("  %d/%d 字节" % (i, n), flush=True))
    elif mode == "submit":
        meta = json.loads(args[2]) if len(args) > 2 else {}
        r = u.upload(path, progress=lambda i, n: print("  %d/%d 字节" % (i, n), flush=True))
        out = {"upload": r, "submit": u.submit(r["filename"], meta)}
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
