# -*- coding: utf-8 -*-
"""Rivulet 若 · B站服务(无头机器人 + 面板 HTTP API)

复用 `fatfish/bili/client.py`(纯标准库 + wbi 签名 + cookie),
不碰浏览器、不抢鼠标键盘 —— 面板里点一下就能看登录态/投稿/评论/回复。

跑法:
    python bili_service.py            # 默认 127.0.0.1:18097

HTTP:
    GET  /api/status                 登录态 / 昵称 / 头像 / 等级 / 硬币 / 关注
    GET  /api/videos?limit=10        我最近的投稿(标题/BV/播放/评论)
    GET  /api/comments?bvid=BVxxx&limit=20   某条视频的评论
    GET  /api/feed?limit=10          首页推荐(证明链路通)
    POST /api/reply                  {bvid, text}  在视频下发一条评论(要 cookie)
    POST /api/reload                 重新读 cookie
"""
from __future__ import annotations

import io
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, "..", ".."))          # ...\workspace
FATFISH = os.path.join(WS, "fatfish")
sys.path.insert(0, os.path.join(FATFISH, "bili"))

CFG_PATH = os.path.join(HERE, "bili.json")
DEFAULT_CFG = {"allow_write": False, "cookie_file": os.path.join(FATFISH, "data", "bili_cookie.json")}
try:
    with open(CFG_PATH, encoding="utf-8") as _f:
        _raw = _f.read()
    CFG = dict(DEFAULT_CFG, **(json.loads(_raw) if _raw.strip() else {}))
except Exception:
    CFG = dict(DEFAULT_CFG)

LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, object]] = {}


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _save_cfg():
    try:
        with io.open(CFG_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps(CFG, ensure_ascii=False, indent=2))
    except Exception:
        pass


def bili():
    """每次拿一个新的客户端(便宜),cookie 变了也能立刻生效。"""
    from client import Bili  # noqa: E402
    return Bili(CFG.get("cookie_file"))


def cached(key: str, ttl: float, fn):
    with LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with LOCK:
        _CACHE[key] = (time.time(), val)
    return val


def api_status() -> dict:
    b = bili()
    out = {"ok": True, "time": now(), "cookie_file": CFG.get("cookie_file"),
           "allow_write": bool(CFG.get("allow_write"))}
    try:
        nav = b.nav() or {}
        if nav.get("isLogin"):
            out.update({
                "logged_in": True,
                "uname": nav.get("uname"),
                "mid": nav.get("mid"),
                "face": nav.get("face"),
                "level": nav.get("level_info", {}).get("current_level"),
                "coins": nav.get("money"),
                "vip": (nav.get("vipStatus") == 1),
            })
        else:
            out.update({"logged_in": False, "error": "cookie 过期了,要重新扫码"})
    except Exception as e:
        out.update({"logged_in": False, "error": str(e)[:200]})
    return out


def api_videos(limit: int = 10) -> dict:
    def _load():
        b = bili()
        nav = b.nav() or {}
        mid = nav.get("mid")
        if not mid:
            raise RuntimeError("没登录,拿不到 mid")
        d = b.get_wbi("https://api.bilibili.com/x/space/wbi/arc/search",
                      {"mid": mid, "ps": min(50, limit), "pn": 1, "order": "pubdate"})
        vl = ((d or {}).get("list") or {}).get("vlist") or []
        items = []
        for v in vl:
            items.append({
                "bvid": v.get("bvid"), "aid": v.get("aid"), "title": v.get("title"),
                "play": v.get("play"), "comment": v.get("comment"),
                "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(v.get("created") or 0)),
                "length": v.get("length"), "pic": v.get("pic"),
            })
        return {"ok": True, "items": items}
    try:
        return cached(f"videos:{limit}", 120, _load)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "items": []}


def api_comments(bvid: str, limit: int = 20) -> dict:
    def _load():
        b = bili()
        aid = b.aid_of(bvid)
        reps = b.comments(aid, pn=1, ps=min(50, limit), sort=2) or []
        items = []
        for r in reps:
            mem = r.get("member") or {}
            items.append({
                "rpid": r.get("rpid"),
                "user": mem.get("uname"),
                "mid": mem.get("mid"),
                "avatar": mem.get("avatar"),
                "text": (r.get("content") or {}).get("message") or "",
                "like": r.get("like") or 0,
                "ctime": time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("ctime") or 0)),
                "replies": len(r.get("replies") or []),
            })
        return {"ok": True, "bvid": bvid, "items": items}
    try:
        return cached(f"cmt:{bvid}:{limit}", 60, _load)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "items": []}


def api_feed(limit: int = 10) -> dict:
    def _load():
        b = bili()
        vids = b.rcmd(ps=limit) or []
        items = [{"bvid": v.get("bvid"), "title": v.get("title"), "owner": (v.get("owner") or {}).get("name"),
                  "play": (v.get("stat") or {}).get("view"), "pic": v.get("pic")} for v in vids]
        return {"ok": True, "items": items}
    try:
        return cached(f"feed:{limit}", 120, _load)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "items": []}


def api_reply(bvid: str, text: str) -> dict:
    if not CFG.get("allow_write"):
        return {"ok": False, "error": "写操作没开(services/bili.json 里 allow_write=false)"}
    if not bvid or not text.strip():
        return {"ok": False, "error": "bvid / text 都要有"}
    try:
        b = bili()
        r = b.reply(bvid, text.strip())
        return {"ok": True, "result": r}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}



# ---------------------------------------------------------------- 投稿 / 稿件管理
PUB = {"running": False, "pct": 0, "log": [], "ok": None, "error": "", "started": "",
       "file": "", "title": "", "dry": False}
PUB_LOCK = threading.Lock()
BILI_DIR = os.path.join(FATFISH, "bili")
PUB_DATA = os.path.join(HERE, "data", "publish")
os.makedirs(PUB_DATA, exist_ok=True)

# 常用分区(初代 分区对照.txt 里挑的硬件/知识区),够用;想加直接改这里
PARTITIONS = [
    {"tid": 188, "name": "科技·数码 → 电脑装机"},
    {"tid": 189, "name": "科技·数码 → 手机平板"},
    {"tid": 190, "name": "科技·数码 → 摄影摄像"},
    {"tid": 191, "name": "科技·数码 → 智能家居"},
    {"tid": 192, "name": "科技·数码 → 其他数码"},
    {"tid": 201, "name": "知识 → 科学科普"},
    {"tid": 202, "name": "知识 → 社科·法律·心理"},
    {"tid": 208, "name": "知识 → 财经商业"},
    {"tid": 209, "name": "知识 → 校园学习"},
    {"tid": 21, "name": "日常 → 生活"},
    {"tid": 160, "name": "生活 → 手工"},
    {"tid": 228, "name": "生活 → 日常"},
]


def api_partitions() -> dict:
    return {"ok": True, "items": PARTITIONS}


def _load_uploads() -> dict:
    try:
        with io.open(os.path.join(FATFISH, "data", "uploads.json"), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {"items": []}
    except Exception:
        return {"items": []}


def api_uploads() -> dict:
    d = _load_uploads()
    items = d.get("items") or []
    out = []
    for it in items[-40:]:
        out.append({
            "at": it.get("at"), "title": it.get("title"), "bvid": it.get("bvid") or "",
            "local": it.get("local") or "", "size": it.get("size") or 0,
            "pending": not bool(it.get("bvid")),
        })
    return {"ok": True, "items": list(reversed(out)),
            "pending": sum(1 for x in out if x["pending"])}


def _pub_log(line: str):
    with PUB_LOCK:
        PUB["log"].append(line)
        if len(PUB["log"]) > 400:
            del PUB["log"][:200]
        m = re.search(r"(\d{1,3})%", line)
        if m:
            PUB["pct"] = max(PUB["pct"], int(m.group(1)))


def _run_publish(video: str, meta: dict, dry: bool):
    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        meta_path = os.path.join(PUB_DATA, "meta-%s.json" % ts)
        with io.open(meta_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2))
        cmd = [sys.executable, os.path.join(BILI_DIR, "publish.py"), video, meta_path]
        if dry:
            cmd.append("--dry")
        _pub_log("$ " + " ".join(cmd))
        proc = subprocess.Popen(cmd, cwd=BILI_DIR, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=0x08000000)
        for raw in iter(proc.stdout.readline, b""):
            try:
                line = raw.decode("utf-8", "replace").rstrip()
            except Exception:
                line = str(raw)
            if line:
                _pub_log(line)
        code = proc.wait()
        with PUB_LOCK:
            PUB["ok"] = (code == 0)
            if code != 0:
                PUB["error"] = "publish.py 退出码 %s" % code
            PUB["running"] = False
            PUB["pct"] = 100 if code == 0 else PUB["pct"]
    except Exception as e:
        with PUB_LOCK:
            PUB["ok"] = False
            PUB["error"] = str(e)[:300]
            PUB["running"] = False


def api_publish(body: dict) -> dict:
    with PUB_LOCK:
        if PUB["running"]:
            return {"ok": False, "error": "上一次投稿还在跑,等它结束"}
    video = str(body.get("file") or "").strip()
    if not video or not os.path.exists(video):
        return {"ok": False, "error": "视频文件不存在:" + (video or "(空)")}
    title = str(body.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "标题不能空"}
    meta = {
        "title": title,
        "desc": str(body.get("desc") or "").strip(),
        "tag": str(body.get("tag") or "").strip(),
        "tid": int(body.get("tid") or 188),
        "cover": str(body.get("cover") or "").strip(),
        "copyright": int(body.get("copyright") or 1),
        "no_reprint": int(body.get("no_reprint") or 1),
        "is_only_self": 1 if body.get("is_only_self") else 0,
    }
    dry = bool(body.get("dry"))
    with PUB_LOCK:
        PUB.update({"running": True, "pct": 0, "log": [], "ok": None, "error": "",
                    "started": now(), "file": video, "title": title, "dry": dry})
    threading.Thread(target=_run_publish, args=(video, meta, dry), daemon=True).start()
    return {"ok": True, "dry": dry}


def api_publish_status() -> dict:
    with PUB_LOCK:
        return {"ok": True, "running": PUB["running"], "pct": PUB["pct"],
                "ok_result": PUB["ok"], "error": PUB["error"], "started": PUB["started"],
                "file": PUB["file"], "title": PUB["title"], "dry": PUB["dry"],
                "log": PUB["log"][-80:]}


def api_edit(body: dict) -> dict:
    if not CFG.get("allow_write"):
        return {"ok": False, "error": "写操作没开(services/bili.json → allow_write=true)"}
    bvid = str(body.get("bvid") or "").strip()
    if not bvid:
        return {"ok": False, "error": "要 bvid"}
    changes = {}
    for k in ("title", "desc", "tag", "cover"):
        v = body.get(k)
        if v is not None and str(v).strip() != "":
            changes[k] = str(v).strip()
    if body.get("tid"):
        changes["tid"] = int(body["tid"])
    if not changes:
        return {"ok": False, "error": "没有要改的字段"}
    try:
        sys.path.insert(0, BILI_DIR)
        import manage as bili_manage  # noqa: E402
        b = bili()
        res = bili_manage.edit(b, bvid, changes, dry=bool(body.get("dry")))
        return {"ok": True, "result": res}
    except Exception as e:
        return {"ok": False, "error": str(e)[:400]}


def api_cover(path: str) -> dict:
    """把本地封面图返回成 base64,给面板预览。"""
    try:
        if not path or not os.path.exists(path):
            return {"ok": False, "error": "图片不存在"}
        data = open(path, "rb").read()
        if len(data) > 6 * 1024 * 1024:
            return {"ok": False, "error": "图片太大(>6MB)"}
        import base64
        ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "bmp": "bmp"}.get(ext, "png")
        return {"ok": True, "b64": base64.b64encode(data).decode(), "mime": mime}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}



# ---------------------------------------------------------------- 每天刷推荐(喜欢就点赞 + 评论 + 通知)
DAILY = {"running": False, "log": [], "results": [], "ok": None, "started": "", "finished": ""}
DAILY_LOCK = threading.Lock()
DAILY_LOG = os.path.join(HERE, "data", "daily_watch.jsonl")
QQ_API = os.environ.get("RIVULET_QQ") or "http://127.0.0.1:18096"
MOOD_API = os.environ.get("RIVULET_MOOD") or "http://127.0.0.1:18099"
PERSONA_API = os.environ.get("RIVULET_PERSONA") or "http://127.0.0.1:18098"

# 硬件/科普类关键词 —— 刷到这些就算“喜欢”,去点赞 + 评论
LIKE_HINTS = ("显卡", "内存", "固态", "主板", "散热", "CPU", "cpu", "装机", "机箱", "电源",
              "笔记本", "显卡", "芯片", "硬盘", "显示器", "键盘", "外设", "DIY", "diy",
              "科普", "拆解", "测评", "评测", "性能", "英伟达", "AMD", "Intel", "台积电",
              "AI", "算力", "显存", "风扇", "水冷", "超频", "服务器")


def _dlog(line: str):
    with DAILY_LOCK:
        DAILY["log"].append(line)
        if len(DAILY["log"]) > 300:
            del DAILY["log"][:150]


def _post(url: str, body: dict, timeout: float = 30):
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _gen_comment(title: str, desc: str, owner: str) -> str:
    """让 DSH 写一句像人说的评论(一句话结论 + 一个可验证的细节)。"""
    try:
        with urllib.request.urlopen(MOOD_API.rstrip("/") + "/api/brief", timeout=4) as r:
            mb = (json.loads(r.read().decode("utf-8")) or {}).get("brief") or ""
    except Exception:
        mb = ""
    try:
        with urllib.request.urlopen(PERSONA_API.rstrip("/") + "/api/persona/brief?channel=bili", timeout=4) as r:
            pb = (json.loads(r.read().decode("utf-8")) or {}).get("brief") or ""
    except Exception:
        pb = ""
    prompt = (pb + "\n\n" if pb else "") + (mb + "\n\n" if mb else "") + ("你在 B 站刷到一条视频,想留一条评论。视频标题:%s。简介:%s。"
              "写一到两句,口语,像真人随手评论:一句自己的判断/结论 + 一个具体的、可验证的细节或追问;"
              "不要客套、不要「学习了」「感谢分享」、不要 @ 人、不要提 AI。直接给评论正文。"
              % (title[:80], (desc or "")[:300]))
    try:
        r = _post(QQ_API.rstrip("/") + "/api/generate", {"prompt": prompt, "max_tokens": 120}, 45)
        if r.get("ok") and r.get("text"):
            return r["text"].strip()[:220]
    except Exception as e:
        _dlog("生成评论失败:%s" % str(e)[:120])
    return ""


def _notify_owner(text: str) -> bool:
    """把「这条看点在哪」发给主人(白名单里的 QQ)。"""
    try:
        r = _post(QQ_API.rstrip("/") + "/api/test_send",
                  {"chat": "private:" + str(CFG.get("owner") or os.environ.get("RIVULET_OWNER") or "2801046701"),
                   "text": text}, 15)
        return bool(r.get("ok"))
    except Exception as e:
        _dlog("通知主人失败:%s" % str(e)[:120])
        return False


def _run_daily(count: int, auto_like: bool, auto_comment: bool, notify: bool, dry: bool):
    try:
        b = bili()
        _dlog("开始刷推荐:取 %d 条" % count)
        vids = b.rcmd(ps=max(5, min(20, count * 2))) or []
        results = []
        picked = 0
        for v in vids:
            if picked >= count:
                break
            bvid = v.get("bvid")
            title = v.get("title") or ""
            if not bvid:
                continue
            blob = title + " " + ((v.get("owner") or {}).get("name") or "")
            liked = any(k in blob for k in LIKE_HINTS)
            if not liked:
                _dlog("· 跳过《%s》(不像我们的题材)" % title[:24])
                continue
            picked += 1
            item = {"bvid": bvid, "title": title, "liked": False, "commented": False,
                    "comment": "", "notified": False, "error": ""}
            # 详情(拿简介)
            desc = ""
            try:
                info = b.view(bvid) or {}
                desc = info.get("desc") or ""
            except Exception:
                pass
            if auto_like and not dry:
                try:
                    b.like(bvid, True)
                    item["liked"] = True
                    _dlog("· 点赞 %s《%s》" % (bvid, title[:24]))
                except Exception as e:
                    item["error"] = "点赞失败:%s" % str(e)[:80]
                    _dlog(item["error"])
            elif auto_like and dry:
                _dlog("· (dry) 会点赞 %s" % bvid)
            if auto_comment:
                text = _gen_comment(title, desc, "")
                if text:
                    item["comment"] = text
                    if not dry:
                        try:
                            b.reply(bvid, text)
                            item["commented"] = True
                            _dlog("· 评论 %s:%s" % (bvid, text[:40]))
                        except Exception as e:
                            item["error"] = "评论失败:%s" % str(e)[:80]
                            _dlog(item["error"])
                    else:
                        _dlog("· (dry) 会评论:%s" % text[:40])
            if notify and not dry:
                msg = ("我在 B 站刷到《%s》(%s)。%s" %
                       (title[:60], bvid, ("这条的看点:" + item["comment"]) if item["comment"] else "觉得你会想看。"))
                item["notified"] = _notify_owner(msg)
                _dlog("· 通知主人:%s" % ("成功" if item["notified"] else "失败"))
            results.append(item)
            time.sleep(2)
        with DAILY_LOCK:
            DAILY["results"] = results
            DAILY["ok"] = True
        try:
            with io.open(DAILY_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": now(), "results": results}, ensure_ascii=False) + "\n")
        except Exception:
            pass
        _dlog("完成:处理 %d 条" % len(results))
    except Exception as e:
        with DAILY_LOCK:
            DAILY["ok"] = False
            DAILY["results"] = []
        _dlog("失败:%s" % str(e)[:200])
    finally:
        with DAILY_LOCK:
            DAILY["running"] = False
            DAILY["finished"] = now()


def api_daily_watch(body: dict) -> dict:
    with DAILY_LOCK:
        if DAILY["running"]:
            return {"ok": False, "error": "上一轮还在跑"}
        DAILY.update({"running": True, "log": [], "results": [], "ok": None,
                      "started": now(), "finished": ""})
    count = int(body.get("count") or 5)
    threading.Thread(target=_run_daily, args=(
        count, bool(body.get("auto_like", True)), bool(body.get("auto_comment", True)),
        bool(body.get("notify", True)), bool(body.get("dry", False))), daemon=True).start()
    return {"ok": True}


def api_daily_status() -> dict:
    with DAILY_LOCK:
        return {"ok": True, "running": DAILY["running"], "log": DAILY["log"][-60:],
                "results": DAILY["results"], "ok_result": DAILY["ok"],
                "started": DAILY["started"], "finished": DAILY["finished"]}



DAILY_STATE = os.path.join(HERE, "data", "daily_state.json")


def _ds_load() -> dict:
    try:
        return json.loads(io.open(DAILY_STATE, encoding="utf-8").read() or "{}")
    except Exception:
        return {}


def _ds_save(d: dict):
    try:
        io.open(DAILY_STATE, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=1))
    except Exception:
        pass


def daily_cfg() -> dict:
    c = CFG.get("daily") or {}
    return {"enabled": bool(c.get("enabled", False)),
            "per_day": int(c.get("per_day", 1)),
            "count": int(c.get("count", 3)),
            "from_hour": int(c.get("from_hour", 10)),
            "to_hour": int(c.get("to_hour", 22)),
            "auto_like": bool(c.get("auto_like", True)),
            "auto_comment": bool(c.get("auto_comment", True)),
            "notify": bool(c.get("notify", True))}


def daily_state() -> dict:
    st = _ds_load()
    cfg = daily_cfg()
    today = time.strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"] = today
        st["count"] = 0
        _ds_save(st)
    return {"ok": True, "config": cfg, "day": st.get("day"),
            "today_runs": int(st.get("count") or 0),
            "last_at": st.get("last_at") or "",
            "next_hint": ("今天已经刷够" if int(st.get("count") or 0) >= cfg["per_day"]
                          else ("在 %d~%d 点之间随机一次" % (cfg["from_hour"], cfg["to_hour"])))}


def _daily_loop():
    """每天自动刷一次推荐(照初代的每日任务)。"""
    time.sleep(60)
    while True:
        try:
            time.sleep(600)
            cfg = daily_cfg()
            if not cfg["enabled"]:
                continue
            st = _ds_load()
            today = time.strftime("%Y-%m-%d")
            if st.get("day") != today:
                st["day"] = today
                st["count"] = 0
                _ds_save(st)
            if int(st.get("count") or 0) >= cfg["per_day"]:
                continue
            h = time.localtime().tm_hour
            if not (cfg["from_hour"] <= h < cfg["to_hour"]):
                continue
            if random.random() > 0.2:          # 一个时段里随机挑一次,别太机械
                continue
            with DAILY_LOCK:
                if DAILY["running"]:
                    continue
            api_daily_watch({"count": cfg["count"], "auto_like": cfg["auto_like"],
                             "auto_comment": cfg["auto_comment"], "notify": cfg["notify"]})
            st["count"] = int(st.get("count") or 0) + 1
            st["last_at"] = now()
            _ds_save(st)
            _dlog("自动刷推荐(第 %d 次/今天)" % st["count"])
        except Exception as e:
            _dlog("自动刷推荐出错:%s" % str(e)[:160])


class Handler(BaseHTTPRequestHandler):
    server_version = "RivuletBili/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code: int, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/api/status":
            return self._send(200, api_status())
        if u.path == "/api/videos":
            return self._send(200, api_videos(int((q.get("limit") or ["10"])[0])))
        if u.path == "/api/comments":
            bvid = (q.get("bvid") or [""])[0]
            return self._send(200, api_comments(bvid, int((q.get("limit") or ["20"])[0])))
        if u.path == "/api/feed":
            return self._send(200, api_feed(int((q.get("limit") or ["10"])[0])))
        if u.path == "/api/partitions":
            return self._send(200, api_partitions())
        if u.path == "/api/uploads":
            return self._send(200, api_uploads())
        if u.path == "/api/publish_status":
            return self._send(200, api_publish_status())
        if u.path == "/api/cover":
            return self._send(200, api_cover((q.get("path") or [""])[0]))
        if u.path == "/api/daily/config":
            return self._send(200, daily_state())
        if u.path == "/api/daily/status":
            return self._send(200, api_daily_status())
        if u.path == "/api/health":
            return self._send(200, {"ok": True, "service": "rivulet-bili"})
        return self._send(404, {"ok": False, "error": "no such route"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        if u.path == "/api/daily/config":
            c = CFG.setdefault("daily", {})
            for k in ("enabled", "auto_like", "auto_comment", "notify"):
                if k in body:
                    c[k] = bool(body[k])
            for k in ("per_day", "count", "from_hour", "to_hour"):
                if body.get(k) is not None:
                    c[k] = int(body[k])
            _save_cfg()
            return self._send(200, daily_state())
        if u.path == "/api/daily/watch":
            return self._send(200, api_daily_watch(body))
        if u.path == "/api/publish":
            return self._send(200, api_publish(body))
        if u.path == "/api/edit":
            return self._send(200, api_edit(body))
        if u.path == "/api/reply":
            return self._send(200, api_reply(body.get("bvid") or "", body.get("text") or ""))
        if u.path == "/api/reload":
            with LOCK:
                _CACHE.clear()
            return self._send(200, api_status())
        return self._send(404, {"ok": False, "error": "no such route"})


def main():
    port = int(os.environ.get("RIVULET_BILI_PORT") or 18097)
    if not os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(CFG, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    threading.Thread(target=_daily_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[bili] http://127.0.0.1:{port}  cookie={CFG.get('cookie_file')}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
