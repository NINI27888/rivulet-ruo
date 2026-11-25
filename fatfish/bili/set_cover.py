# -*- coding: utf-8 -*-
"""给已发布的视频补/换封面:先上传封面图,再走 edit 接口。"""
import json
import os
import sys
import time

BILI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BILI)
from client import Bili, BiliError  # noqa: E402
from manage import current, csrf  # noqa: E402
from upload import Uploader  # noqa: E402

WS = os.path.abspath(os.path.join(BILI, "..", ".."))
COV = os.path.join(WS, "video", "out", "covers")

TARGETS = [
    ("BV1prsT6RE73", os.path.join(COV, "H13_消失了九天.png")),
    ("BV1ArsT6REqt", os.path.join(COV, "H14_五个件.png")),
]


def set_cover(b, u, bv, img):
    cur = current(b, bv)
    print(bv, "aid", cur["aid"], "title", cur["title"][:30], flush=True)
    c = u.cover(img)
    url = ((c.get("data") or {}).get("url")) or ""
    print("  cover url:", url or c, flush=True)
    if not url:
        return False
    body = {
        "aid": cur["aid"],
        "title": cur["title"],
        "desc": cur["desc"],
        "tag": cur["tag"],
        "tid": int(cur["tid"] or 188),
        "cover": url,
        "copyright": int(cur["copyright"] or 1),
        "source": cur["source"],
        "no_reprint": 1,
        "interactive": 0,
        "act_reserve_create": 0,
        "no_disturbance": 0,
        "subtitle": {"open": 0, "lan": ""},
        "dolby": 0,
        "lossless_music": 0,
        "up_selection_reply": False,
        "up_close_reply": False,
        "up_close_danmu": False,
        "web_os": 2,
        "videos": [{"cid": v["cid"], "title": v["title"], "desc": v.get("desc", "")} for v in cur["videos"]],
    }
    url2 = "https://member.bilibili.com/x/vu/web/edit?t=%d&csrf=%s" % (int(time.time() * 1000), csrf(b))
    r, _ = u._req(url2, data=json.dumps(body).encode(), method="POST",
                  headers={"Content-Type": "application/json"})
    print("  edit:", r.decode("utf-8", "replace")[:200], flush=True)
    return True


def main():
    b = Bili()
    u = Uploader(b)
    for bv, img in TARGETS:
        if not os.path.exists(img):
            print("跳过(没有封面图)", bv, img, flush=True)
            continue
        try:
            set_cover(b, u, bv, img)
        except BiliError as e:
            print("  ERR", str(e)[:200], flush=True)
        time.sleep(8)


if __name__ == "__main__":
    main()
