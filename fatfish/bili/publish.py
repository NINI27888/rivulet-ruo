# -*- coding: utf-8 -*-
"""一条命令投稿。分得开:先传、再提交,传和提交各自能单独跑。

    python publish.py <视频路径> <meta.json>            传 + 提交
    python publish.py <视频路径> <meta.json> --dry       只传,不提交(不占投稿位)
    python publish.py --submit <filename|bvid|关键词> <meta.json>
                                                        只提交(传过的,别再传一遍)
    python publish.py --pending                        看看有哪些传了还没提交

meta.json(见 meta.example.json):
    {"title": "...", "desc": "...", "tag": "a,b,c", "tid": 188,
     "cover": "封面图路径(可空)", "copyright": 1, "no_reprint": 1, "is_only_self": 0}

为什么要拆成两步:B站 有投稿频次限制(21564「投稿过于频繁,请24小时后重试」)。
传完卡在提交这一步时,文件已经在 B站 那边了,第二天直接用 --submit 接着提,不用重传。
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import Bili  # noqa: E402
from upload import Uploader  # noqa: E402
import uploads  # noqa: E402  台账:存 filename,以后这条稿才改得动


def _progress():
    """按 5% 一档打印,视频大也不刷屏。"""
    last = [-1]

    def f(done, total):
        if not total:
            return
        pct = int(done * 100 / total)
        if pct >= last[0] + 5 or pct >= 100:
            last[0] = pct
            print("  传了 %d%%   %d/%d 字节" % (pct, done, total), flush=True)
    return f


def _after_submit(meta, r, filename, bvid, aid):
    """投稿成功后:台账补 bvid、记忆里记一笔。"""
    uploads.attach_bvid(filename, bvid, aid)
    print("成功,bvid:", bvid)
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "core"))
        from memory import 记忆
        m = 记忆()
        m.记视频(bvid, aid=aid, 标题=meta.get("title", ""),
               分区=meta.get("tid", ""), 标签=meta.get("tag", ""),
               仅自己可见=bool(meta.get("is_only_self")),
               文件名=filename)
        m.记("投稿成功 %s《%s》" % (bvid, meta.get("title", "")), 来源="B站", 标签="投稿",
             类别="B站")
        m.生成()
        print("记忆已记:", bvid)
    except Exception as e:
        print("记忆没记上(不影响投稿):", repr(e)[:150])


def _load_meta(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    dry = "--dry" in argv

    if "--pending" in argv:
        rows = uploads.pending()
        if not rows:
            print("没有传了没提交的,台账是干净的。")
            return 0
        for x in rows:
            print("%s | %s | %s | %s" % (x.get("at", "")[:16], x.get("filename"),
                                         x.get("size", 0), x.get("title") or ""))
        return 0

    if "--submit" in argv:
        if len(args) < 2:
            print(__doc__)
            return 2
        key, meta_path = args[0], args[1]
        rec = uploads.find(key)
        b = Bili()
        if not b.logged_in:
            print("没登录,先跑 qr_login.py")
            return 2
        meta = _load_meta(meta_path)
        if rec:
            filename = rec.get("filename") or ""
            print("台账里找到了:%s" % rec.get("at"))
        else:
            filename = key
            print("台账里没有,就当你给的是 filename:%s" % filename)
        print("提交 filename =", filename)
        up = Uploader(b)
        meta2 = dict(meta)
        if meta2.get("cover") and os.path.exists(meta2["cover"]):
            # meta 里的 cover 是本地图路径:直接丢给提交接口会回 21008
            # (封面必须由页面进行上传,不支持外部链接)。先传上去换成 url。
            c = up.cover(meta2["cover"])
            _u = ((c.get("data") or {}).get("url")) or ""
            print("封面:", _u or c)
            if _u:
                meta2["cover"] = _u
        res = up.submit(filename, meta2)
        print("提交:", json.dumps(res, ensure_ascii=False, indent=2))
        if res.get("code") == 0:
            d = res.get("data") or {}
            _after_submit(meta, res, filename, d.get("bvid"), d.get("aid") or 0)
            return 0
        print("失败,code:", res.get("code"), res.get("message"))
        if res.get("code") == 21564:
            print("(只是限频。文件已经在 B站 了,过几个小时用 --submit 接着提,不用重传。)")
        return 1

    if len(args) < 2:
        print(__doc__)
        return 2
    video, meta_path = args[0], args[1]
    meta = _load_meta(meta_path)
    if not meta.get("title"):
        print("meta 里没有 title")
        return 2

    b = Bili()
    if not b.logged_in:
        print("没登录,先跑 qr_login.py")
        return 2
    u = Uploader(b)

    cover_url = ""
    if meta.get("cover") and os.path.exists(meta["cover"]):
        c = u.cover(meta["cover"])
        cover_url = ((c.get("data") or {}).get("url")) or ""
        print("封面:", cover_url or c)

    print("上传 %s  %d 字节" % (os.path.basename(video), os.path.getsize(video)))
    r = u.upload(video, progress=_progress())
    print("传完了: filename=%s" % r.get("filename"))
    print("        upos_uri=%s" % r.get("upos_uri"))
    uploads.remember(r["filename"], local=os.path.abspath(video), size=r.get("size") or 0,
                     biz_id=r.get("biz_id") or 0, title=meta.get("title", ""))
    print("台账已记:", uploads.PATH)

    if dry:
        print("\n--dry:不提交。文件已经在你 B站 账号底下了,以后接着提:")
        print("  python publish.py --submit %s %s" % (r["filename"], meta_path))
        return 0

    meta2 = dict(meta)
    meta2["cover"] = cover_url or meta.get("cover", "")
    res = u.submit(r["filename"], meta2)
    print("提交:", json.dumps(res, ensure_ascii=False, indent=2))
    if res.get("code") == 0:
        d = res.get("data") or {}
        _after_submit(meta2, res, r["filename"], d.get("bvid"), d.get("aid") or 0)
        return 0
    print("失败,code:", res.get("code"), res.get("message"))
    if res.get("code") == 21564:
        print("(只是限频。文件已经传好了,过几个小时接着提,不用重传:")
        print("  python publish.py --submit %s %s)" % (r["filename"], meta_path))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
