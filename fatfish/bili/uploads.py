# -*- coding: utf-8 -*-
"""投稿台账:每次上传成功,把 filename(upos 路径)记下来。

为什么要有这个:改稿件接口 `x/vu/web/edit` 必须带 `videos[].filename`,
而所有只读接口都不返回它(06:20 挖到底了,见 项目状态.md)。
所以只能自己在投稿时存一份 —— 存了,以后这条稿就能改。

文件:fatfish/data/uploads.json
    {"items": [{"at": "...", "filename": "ugcfxever/xxx.mp4", "size": 18923915,
                "biz_id": ..., "local": "D:\\...\\04_xxx.mp4",
                "title": "...", "bvid": "BV...", "aid": 0}, ...]}

    python uploads.py list                # 全部
    python uploads.py pending             # 传了还没提交的
    python uploads.py find <关键词|bvid>   # 找一条
"""

import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
PATH = os.path.join(DATA, "uploads.json")


def _load():
    try:
        with io.open(PATH, encoding="utf-8") as f:
            d = json.load(f)
    except (IOError, OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("items", [])
    return d


def _save(d):
    if not os.path.isdir(DATA):
        os.makedirs(DATA)
    with io.open(PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=1) + "\n")


def remember(filename, local="", size=0, biz_id=0, title="", bvid="", aid=0):
    """上传/投稿成功后调一次。同 filename 覆盖旧的。"""
    d = _load()
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "filename": filename,
           "size": size, "biz_id": biz_id, "local": local,
           "title": title, "bvid": bvid, "aid": aid}
    items = [x for x in d["items"] if x.get("filename") != filename]
    items.append(rec)
    d["items"] = items[-200:]
    _save(d)
    return rec


def attach_bvid(filename, bvid, aid=0):
    """提交成功后,把 bvid/aid 补到那条记录上。"""
    d = _load()
    for x in d["items"]:
        if x.get("filename") == filename:
            x["bvid"] = bvid
            x["aid"] = aid
            _save(d)
            return x
    return None


def find(key):
    d = _load()
    key = (key or "").strip()
    for x in reversed(d["items"]):
        hay = " ".join(str(x.get(k) or "") for k in ("filename", "bvid", "title", "local"))
        if key and key in hay:
            return x
    return None


def filename_of(bvid):
    x = find(bvid)
    return (x or {}).get("filename") or ""


def pending():
    """传上去了、但还没提交成稿件的(没有 bvid)。"""
    d = _load()
    return [x for x in d["items"] if not x.get("bvid")]


def main(argv):
    if not argv or argv[0] == "list":
        d = _load()
        if not d["items"]:
            print("台账是空的。下次投稿成功就会有一条。")
            return 0
        for x in d["items"]:
            print("%s | %-14s | %s | %s" % (x.get("at", "")[:16], x.get("bvid") or "-",
                                            x.get("filename"), x.get("title") or ""))
        return 0
    if argv[0] == "pending":
        rows = pending()
        if not rows:
            print("没有传了没提交的。")
            return 0
        for x in rows:
            print("%s | %s | %s | %s" % (x.get("at", "")[:16], x.get("filename"),
                                         x.get("size", 0), x.get("title") or ""))
        return 0
    if argv[0] == "find":
        x = find(argv[1] if len(argv) > 1 else "")
        print(json.dumps(x, ensure_ascii=False, indent=2) if x else "没找到")
        return 0 if x else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
