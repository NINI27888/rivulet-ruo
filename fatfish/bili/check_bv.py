import os
# -*- coding: utf-8 -*-
import io, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import Bili, BiliError

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bili_bv_check.txt")
L = []
def w(*a):
    L.append(" ".join(str(x) for x in a))

b = Bili()
w("login:", b.logged_in)
for bv in ("BV1TuHM6RE5y", "BV1XW1fFGELa", "BV1oaM5m3gM", "BV1NzSGEcb3r", "BV1NzSGEcb3R"):
    try:
        v = b.view(bv)
        w(bv, "OK", v.get("title"), "aid=", v.get("aid"), "状态=", v.get("state"), "stat=", (v.get("stat") or {}).get("view"))
    except BiliError as e:
        w(bv, "ERR", e)
# 自己的投稿列表(空间)
try:
    d = b.get("https://api.bilibili.com/x/space/wbi/arc/search",
              {"mid": b.cookie.get("DedeUserID"), "pn": 1, "ps": 10, "order": "pubdate"})
    vl = ((d or {}).get("list") or {}).get("vlist") or []
    w("空间投稿:", len(vl), "条")
    for x in vl:
        w("   ", x.get("bvid"), x.get("title"), x.get("play"))
except BiliError as e:
    w("空间接口 ERR", e)
io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print("ok")
