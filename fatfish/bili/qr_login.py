# -*- coding: utf-8 -*-
"""Generate and check a Bilibili phone login QR code."""
import io
import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATE = DATA / "qr_login.json"
PNG = DATA / "qr.png"
COOKIE = DATA / "bili_cookie.json"
PASSPORT = "https://passport.bilibili.com/x/passport-login/web/qrcode/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
COOKIE_NAMES = {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid", "buvid3", "buvid4"}
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _call(path):
    request = urllib.request.Request(PASSPORT + path,
                                     headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    with _OPENER.open(request, timeout=8) as response:
        return json.load(response), response.headers.get_all("Set-Cookie") or []


def create_code():
    import segno

    result, _ = _call("generate")
    info = result.get("data") or {}
    if result.get("code") != 0 or not info.get("url") or not info.get("qrcode_key"):
        raise RuntimeError("B 站暂时没有生成二维码，请稍后重试。")
    png = io.BytesIO()
    segno.make(info["url"], error="m").save(png, kind="png", scale=8, border=4)
    return info["qrcode_key"], png.getvalue()


def check_code(key):
    result, cookies = _call("poll?" + urllib.parse.urlencode({"qrcode_key": key}))
    info = result.get("data") or {}
    code = info.get("code")
    if result.get("code") != 0 or code not in (0, 86101, 86090, 86038):
        raise RuntimeError("B 站暂时无法确认登录，请重新扫码。")
    if code != 0:
        return {86101: "qrcode", 86090: "scanned", 86038: "expired"}[code], {}
    values = {}
    for header in cookies:
        name, _, value = header.split(";", 1)[0].partition("=")
        if name.strip() in COOKIE_NAMES:
            values[name.strip()] = value.strip()
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(info.get("url") or "").query)
    for name in COOKIE_NAMES & query.keys():
        values.setdefault(name, query[name][0])
    if not values.get("SESSDATA"):
        raise RuntimeError("手机已确认，但 B 站没有返回登录凭证，请重新扫码。")
    return "ok", values


def save_login(values, path=COOKIE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    pending = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def gen():
    key, png = create_code()
    DATA.mkdir(parents=True, exist_ok=True)
    PNG.write_bytes(png)
    STATE.write_text(json.dumps({"qrcode_key": key}), encoding="utf-8")
    print("二维码已生成", flush=True)


def poll(seconds=180):
    key = json.loads(STATE.read_text(encoding="utf-8"))["qrcode_key"]
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        state, values = check_code(key)
        if state == "ok":
            save_login(values)
            print("登录成功", flush=True)
            return True
        if state == "expired":
            print("二维码已过期", flush=True)
            return False
        time.sleep(2)
    print("扫码等待超时", flush=True)
    return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gen"
    try:
        if cmd == "gen":
            gen()
        elif cmd == "poll":
            sys.exit(0 if poll(int(sys.argv[2]) if len(sys.argv) > 2 else 180) else 1)
    except (OSError, RuntimeError, ValueError):
        print("B 站扫码操作失败，请重试。", file=sys.stderr)
        sys.exit(1)
