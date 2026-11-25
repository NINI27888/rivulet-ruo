# -*- coding: utf-8 -*-
"""稿件管理:查 / 改 / 删。跟投稿同一套 cookie + csrf。

    python manage.py list                       # 我的稿件(创作中心接口,字段全)
    python manage.py listjson                   # 同上,但吐 JSON(给面板读)
    python manage.py info <bvid>                # 一条稿件的完整信息(含 filename 有没有)
    python manage.py status <bvid>              # 审核状态
    python manage.py desc <bvid> <新简介文件>    # 只改简介(从文件读,省得命令行转义)
    python manage.py edit <bvid> '<json>'       # 改标题/简介/标签/封面/分区
    python manage.py edit <bvid> '<json>' --dry # 只打印要发的东西,不发
    python manage.py delete <bvid> --yes        # 删稿(真删,问过他点头再加 --yes)
                                                被 B站 人机验证挡住时会打印手动步骤并帮你开好页面

edit 的 json 可以只写要改的字段,没写的从当前稿件带过来 —— edit 是全量覆盖。

**改稿的硬前提**:`videos[].filename`(当初上传的文件路径)。
只读接口全都不给这个字段(2026-10-29 挖到底,见 项目状态.md),
所以只能从本地台账 `fatfish/data/uploads.json` 拿 —— 由 publish.py 在上传成功时写。
台账里没有的老稿,改不了;要改只能手动去创作中心点。

`cover` 除了 B站 的图片 URL,也可以直接给**本地图片路径**(jpg/png),
edit 会先把它传到 B站 换回 URL 再提交。`--dry` 时只标出来,不上传。
"""

import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import Bili, BiliError  # noqa: E402
from upload import Uploader  # noqa: E402
import uploads  # noqa: E402

EDIT = "https://member.bilibili.com/x/vu/web/edit"
DELETE = "https://member.bilibili.com/x/web/archive/delete"   # 2026-10-29 实测:vu/web/delete 是 404
ARCHIVES = ("https://member.bilibili.com/x/web/archives"
            "?pn=1&ps=50&status=is_pubing,pubed,not_pubed")


def csrf(b):
    c = b.cookie.get("bili_jct", "")
    if not c:
        raise BiliError("cookie 里没有 bili_jct,改不了")
    return c


def archives(b):
    """创作中心的稿件列表:一次拿到标题/简介/标签/分区/封面/状态/is_only_self。"""
    d = b.get(ARCHIVES) or {}
    out = []
    for a in d.get("arc_audits") or []:
        ar = dict(a.get("Archive") or {})
        ar["_cid_list"] = a.get("cid_list") or []
        out.append(ar)
    return out


def find_archive(b, bvid):
    for ar in archives(b):
        if ar.get("bvid") == bvid:
            return ar
    return {}


def tags_of(b, bvid):
    """view 不返回 tag,单独查 —— 否则 edit 会把手写标签清空。"""
    try:
        d = b.get("https://api.bilibili.com/x/tag/archive/tags", {"bvid": bvid}) or []
        return ",".join(x.get("tag_name", "") for x in d if x.get("tag_name"))
    except BiliError:
        return ""


def current(b, bvid):
    """把当前稿件摊平成 edit 接口要的形状。"""
    v = b.view(bvid)
    ar = find_archive(b, bvid)
    pages = v.get("pages") or []
    fn = uploads.filename_of(bvid)
    videos = [{"filename": fn if i == 0 else "",
               "title": v.get("title"), "desc": "", "cid": p.get("cid")}
              for i, p in enumerate(pages)]
    return {
        "aid": v.get("aid"),
        "bvid": v.get("bvid"),
        "title": ar.get("title") or v.get("title"),
        "desc": ar.get("desc") if ar.get("desc") is not None else v.get("desc"),
        "tag": ar.get("tag") or tags_of(b, bvid),
        "tid": ar.get("tid") or v.get("tid"),
        "cover": ar.get("cover") or v.get("pic"),
        "copyright": ar.get("copyright", v.get("copyright")),
        "source": ar.get("source") or v.get("source") or "",
        "is_only_self": ar.get("is_only_self", 0),
        "state": v.get("state"),
        "state_desc": ar.get("state_desc") or "",
        "stat": v.get("stat"),
        "videos": videos,
        "filename": fn,
    }


def build(b, bvid, changes):
    cur = current(b, bvid)
    if not cur["filename"]:
        raise BiliError(
            "这条稿在本地台账里没有 filename,改不了。\n"
            "  原因:编辑接口必须带 videos[].filename,而所有只读接口都不返回它。\n"
            "  台账:%s\n"
            "  老稿只能手动去创作中心改;以后用 publish.py 投稿的会自动记上。" % uploads.PATH)
    return {
        "aid": cur["aid"],
        "title": changes.get("title", cur["title"]),
        "desc": changes.get("desc", cur["desc"]),
        "tag": changes.get("tag", cur["tag"]),
        "tid": int(changes.get("tid", cur["tid"] or 188)),
        "cover": changes.get("cover", cur["cover"]),
        "copyright": int(changes.get("copyright", cur["copyright"] or 1)),
        "source": changes.get("source", cur["source"]),
        "videos": cur["videos"],
        "no_reprint": int(changes.get("no_reprint", 1)),
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
        "csrf": csrf(b),
    }


def edit(b, bvid, changes, dry=False):
    ch = dict(changes or {})
    cov = ch.get("cover")
    if cov and isinstance(cov, str) and os.path.isfile(cov):
        # 本地图片 → 先传上去换 URL(只有真发的时候才传,dry 不动手)
        if dry:
            ch["cover"] = "<本地图片,提交时先上传:%s>" % os.path.basename(cov)
        else:
            res = Uploader(b).cover(cov)
            url = ((res or {}).get("data") or {}).get("url") if isinstance(res, dict) else ""
            if not url:
                raise BiliError("封面上传没成:%s" % json.dumps(res, ensure_ascii=False)[:300])
            ch["cover"] = url
    body = build(b, bvid, ch)
    if dry:
        body2 = dict(body)
        body2["csrf"] = "***"
        return {"dry": True, "would_post": body2}
    u = Uploader(b)
    url = "%s?t=%d&csrf=%s" % (EDIT, int(time.time() * 1000), csrf(b))
    r, _ = u._req(url, data=json.dumps(body).encode(), method="POST",
                  headers={"Content-Type": "application/json"})
    return json.loads(r.decode("utf-8", "replace"))


def delete(b, bvid):
    aid = b.view(bvid).get("aid")
    u = Uploader(b)
    url = "%s?aid=%s&csrf=%s" % (DELETE, aid, csrf(b))
    r, _ = u._req(url, data=b"", method="POST")
    return json.loads(r.decode("utf-8", "replace"))


# 删稿被人机验证挡回来时会变成这样:后台打不通,但不该让人卡在这里。
CAPTCHA_CODES = (340022, 340021, 34002, -401)
ARTICLE_PAGE = "https://member.bilibili.com/platform/upload-manager/article"


def open_article_page():
    """帮他打开创作中心的稿件管理页(Windows 直接交给默认浏览器)。"""
    try:
        os.startfile(ARTICLE_PAGE)  # noqa: S606  只在 Windows 上跑
        print("  已经把稿件管理页给你开好了。")
    except Exception as e:
        print("  (浏览器没开出来:%r —— 自己打开这个地址:%s)" % (e, ARTICLE_PAGE))


def explain_blocked(res, bvid, title=""):
    """删不掉的时候说人话:为什么、去哪点、点哪个。"""
    code = res.get("code")
    if code not in CAPTCHA_CODES:
        return False
    name = "《%s》" % title if title else "你要删的那条"
    print("\n这条接口没删成(它要人机验证,后台过不去)。**手动删一次,就两下**:")
    print("  1. 稿件管理页里找到 %s(bvid %s)那一行" % (name, bvid))
    print("  2. 最右边「更多」→「删除」→ 确认。")
    print("  B站 自己的稿子删了进回收站,30 天内还能捞回来,不用怕点错。")
    open_article_page()
    return True


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    b = Bili()
    cmd = argv[0]
    dry = "--dry" in argv
    argv = [a for a in argv if a != "--dry"]
    try:
        if cmd == "list":
            rows = archives(b)
            if not rows:
                print("(没有稿件)")
            for ar in rows:
                print("%-14s %-3s %s%s" % (ar.get("bvid"), ar.get("state"),
                                           "[仅自己可见] " if ar.get("is_only_self") else "",
                                           ar.get("title")))
                print("               %s | %s | 台账:%s" % (
                    ar.get("state_desc") or "", (ar.get("tag") or "")[:40],
                    "有" if uploads.filename_of(ar.get("bvid") or "") else "没有"))
        elif cmd == "listjson":
            out = []
            for ar in archives(b):
                bv = ar.get("bvid") or ""
                out.append({
                    "bvid": bv,
                    "aid": ar.get("aid"),
                    "title": ar.get("title") or "",
                    "state": ar.get("state"),
                    "state_desc": ar.get("state_desc") or "",
                    "tag": ar.get("tag") or "",
                    "tid": ar.get("tid"),
                    "cover": ar.get("cover") or "",
                    "desc": ar.get("desc") or "",
                    "is_only_self": int(ar.get("is_only_self") or 0),
                    "can_edit": bool(uploads.filename_of(bv)),
                })
            print(json.dumps(out, ensure_ascii=False))
        elif cmd == "info":
            c = current(b, argv[1])
            c["desc"] = (c["desc"] or "")[:120] + "..."
            print(json.dumps(c, ensure_ascii=False, indent=2))
        elif cmd == "status":
            c = current(b, argv[1])
            print("bvid", c["bvid"], "aid", c["aid"], "state", c["state"], c["state_desc"],
                  "| 能改:", "是" if c["filename"] else "否(台账里没有 filename)")
        elif cmd == "desc":
            with io.open(argv[2], encoding="utf-8") as f:
                newdesc = f.read().strip()
            r = edit(b, argv[1], {"desc": newdesc}, dry=dry)
            print(json.dumps(r, ensure_ascii=False, indent=2))
        elif cmd == "edit":
            r = edit(b, argv[1], json.loads(argv[2]), dry=dry)
            print(json.dumps(r, ensure_ascii=False, indent=2))
        elif cmd == "delete":
            if "--yes" not in argv:
                print("删稿要加 --yes(真删了收不回来)。先看一遍:python manage.py info %s" % argv[1])
                return 2
            res = delete(b, argv[1])
            print(json.dumps(res, ensure_ascii=False, indent=2))
            if res.get("code") == 0:
                print("删掉了。")
                return 0
            title = ""
            try:
                title = (b.view(argv[1]) or {}).get("title") or ""
            except Exception:
                pass
            if not explain_blocked(res, argv[1], title):
                print("失败,code:", res.get("code"), res.get("message"))
            return 1
        else:
            print(__doc__)
            return 2
    except BiliError as e:
        print("ERR", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
