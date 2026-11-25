# -*- coding: utf-8 -*-
"""B站 扫码登录:生成二维码 → 渲染 PNG → 轮询登录结果 → 存 cookie。

分两步跑(因为要把二维码发给泥泥,他扫完再轮询):
    python qr_login.py gen      # 生成 + 渲染,写 data/qr.png 和 data/qr_login.json
    python qr_login.py poll     # 轮询到登录成功,把 cookie 写进 data/bili_cookie.json
"""

import io
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
STATE = os.path.join(DATA, "qr_login.json")
PNG = os.path.join(DATA, "qr.png")
COOKIE = os.path.join(DATA, "bili_cookie.json")
NODE = (os.environ.get("NODE_EXE") or "")
if not NODE:
    _p = os.path.join(ROOT, "NapCat", "node.exe")            # 包里自带的
    if os.path.isfile(_p):
        NODE = _p
if not NODE:
    try:
        import shutil as _sh
        NODE = _sh.which("node") or ""
    except Exception:
        NODE = ""
if not NODE:
    NODE = "node"
QRLIB = os.path.join(HERE, "qrlib")                          # 自带的 qrcode 模块
MAKE_QR = os.path.join(HERE, "make_qr.js")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0")

LOG = os.path.join(ROOT, "日志", "qr_login.log")
L = []


def w(*a):
    L.append(" ".join(str(x) for x in a))
    print(L[-1])


def flush():
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        io.open(LOG, "w", encoding="utf-8").write(chr(10).join(L) + chr(10))
    except Exception:
        pass   # 日志写不了不影响主流程


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def gen():
    os.makedirs(DATA, exist_ok=True)
    d = get_json("https://passport.bilibili.com/x/passport-login/web/qrcode/generate")
    if d.get("code") != 0:
        w("生成失败", d)
        flush()
        return
    data = d["data"]
    url = data["url"]
    key = data["qrcode_key"]
    io.open(STATE, "w", encoding="utf-8").write(json.dumps({"qrcode_key": key, "url": url}, ensure_ascii=False))
    w("url =", url)
    w("key =", key)
    # 生成二维码:优先纯 Python(segno),不再依赖 node + npm 包
    _ok = False
    try:
        import segno
        segno.make(url, error="m").save(PNG, scale=10, border=2)
        _ok = os.path.exists(PNG) and os.path.getsize(PNG) > 0
        w("segno ok =", _ok)
    except Exception as _e:
        w("segno 不可用:", _e)
    if not _ok:
        env = dict(os.environ)
        if os.path.isdir(QRLIB):
            old = env.get("NODE_PATH", "")
            env["NODE_PATH"] = QRLIB + (os.pathsep + old if old else "")
        w("node =", NODE)
        r = subprocess.run([NODE, MAKE_QR, url, PNG], capture_output=True, text=True, env=env)
        w("node rc =", r.returncode, r.stdout.strip()[:200], r.stderr.strip()[:200])
        w("png exists =", os.path.exists(PNG), os.path.getsize(PNG) if os.path.exists(PNG) else 0)
    flush()


def poll(seconds=180):
    st = json.load(io.open(STATE, encoding="utf-8"))
    key = st["qrcode_key"]
    url = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=" + urllib.parse.quote(key)
    end = time.time() + seconds
    last = None
    while time.time() < end:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode("utf-8", "replace")
                cookies = r.headers.get_all("Set-Cookie") or []
        except Exception as e:
            w("轮询出错", repr(e))
            time.sleep(3)
            continue
        d = json.loads(raw)
        code = (d.get("data") or {}).get("code")
        if code != last:
            w("状态码:", code, d.get("data", {}).get("message"))
            last = code
        if code == 0:
            ck = {}
            for c in cookies:
                kv = c.split(";")[0]
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    ck[k.strip()] = v.strip()
            # 有些字段在 data.url 的 query 里
            u = (d.get("data") or {}).get("url") or ""
            if u:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
                for k, v in q.items():
                    ck.setdefault(k, v[0])
            io.open(COOKIE, "w", encoding="utf-8").write(json.dumps(ck, ensure_ascii=False, indent=2))
            w("登录成功,cookie 字段:", sorted(ck.keys()))
            flush()
            return True
        if code == 86038:
            w("二维码过期了")
            flush()
            return False
        time.sleep(2)
    w("超时")
    flush()
    return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gen"
    try:
        if cmd == "gen":
            gen()
        else:
            poll(int(sys.argv[2]) if len(sys.argv) > 2 else 180)
    except Exception as e:
        import traceback
        w("FATAL", repr(e))
        w(traceback.format_exc())
        flush()
