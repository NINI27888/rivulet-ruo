# -*- coding: utf-8 -*-
r"""抓一份分区表,写成「分区对照.txt」(给人看的中文表)。

    python bili\partitions.py

不需要登录,公开接口。
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import Bili  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "分区对照.txt")

URLS = [
    "https://api.bilibili.com/x/web-interface/archive/partitions",
    "https://member.bilibili.com/x/vu/web/partition",
    "https://api.bilibili.com/x/v2/archive/partition",
]


def main():
    b = Bili()
    data = None
    for u in URLS:
        try:
            d = b.get(u)
            if d and isinstance(d, list) and d:
                data = d
                print("用了", u)
                break
        except Exception as e:
            print("跳过", u, repr(e)[:80])
    if not data:
        print("没抓到,保留原文件不动。")
        return 1

    lines = ["# B站 投稿分区对照(投稿时菜单会问「分区编号」)",
             "# 左边是编号,投稿时填它。带 * 的是常用。", ""]
    for top in data:
        name = top.get("name") or top.get("tname") or "?"
        tid = top.get("tid") or top.get("id") or ""
        lines.append("%s   %s (%s)" % (str(tid).ljust(5), name, "主分区"))
        for sub in (top.get("sub") or top.get("children") or []):
            sname = sub.get("name") or sub.get("tname") or "?"
            stid = sub.get("tid") or sub.get("id") or ""
            lines.append("   %s   %s" % (str(stid).ljust(5), sname))
        lines.append("")
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("写好", OUT, len(lines), "行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
