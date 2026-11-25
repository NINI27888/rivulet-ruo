# -*- coding: utf-8 -*-
"""每天自己干的活。

    python daily.py watch   # 刷 5 条推荐视频:标题/UP/播放 + 有字幕的读前 20 行
    python daily.py stats   # 查我自己四条视频的数据,追加到 data/stats.csv
    python daily.py tidy    # 把今天的记录压成一句写进 data/memory.json
    python daily.py c6      # 刷完的候选排进队列(不立刻做)
    python daily.py c6tick  # 调度器每分钟叫一次:到点做一条(点赞 + 评论 + @主人)
    python daily.py c6q     # 看队列里排了什么

所有记录落在 fatfish/data/ 下面,和 QQ 那边共用一份。
"""

import csv
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import Bili, BiliError  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
WATCH = os.path.join(DATA, "watch")
STATS = os.path.join(DATA, "stats.csv")
PUSHED = os.path.join(WATCH, "pushed.json")     # 已经发过 QQ 的 bvid,一天一清

sys.path.insert(0, os.path.join(ROOT, "core"))
try:                                  # 记忆:QQ 和 B站 共用那一份
    from memory import 记忆  # noqa: E402
except Exception:                     # 万一 core 没拷过来,也不耽误干活
    记忆 = None


def mem():
    if 记忆 is None:
        return None
    try:
        return 记忆()
    except Exception:
        return None


def ensure():
    for d in (DATA, WATCH):
        os.makedirs(d, exist_ok=True)


def jload(p, default):
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def jsave(p, obj):
    ensure()
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def today():
    return time.strftime("%Y-%m-%d")


# ---------- 偏好(10-31:泥泥要能在面板上调「爱看什么、各占多重」) ----------

def _prefs():
    """从 设置.txt 读偏好方向和各方向的权重。读不到就返回空,照旧按首页顺序刷。"""
    try:
        from config import load
        c = load()
    except Exception:
        return [], {}
    cats = [x for x in re.split(r"[,，、;；\s]+", str(c.get("刷视频偏好") or "")) if x]
    w = {}
    for part in re.split(r"[,，、;；\s]+", str(c.get("刷视频偏好权重") or "")):
        if not part:
            continue
        k, sep, v = (part.partition("=") if "=" in part else part.partition(":"))
        if not sep:
            continue
        try:
            w[k.strip()] = float(v)
        except Exception:
            pass
    return cats, w


def _score(it, cats, w):
    hay = " ".join([str(it.get("tname") or ""), str(it.get("title") or ""),
                    str(it.get("desc") or "")]).lower()
    s = 0.0
    for c in cats:
        if c and c.lower() in hay:
            s += float(w.get(c, 1.0))
    return s


# ---------- 分享到 QQ(11-01 07:1x,C3) ----------

def _fmt_play(n):
    try:
        n = int(n or 0)
    except Exception:
        return ""
    if n >= 100000000:
        return "%.1f亿" % (n / 100000000.0)
    if n >= 10000:
        return "%.1f万" % (n / 10000.0)
    return str(n)


def _share_score(row, cats, w):
    """值不值得单独发他一条:偏好命中几分 + 播放量按数量级加分。"""
    s = _score({"tname": row.get("tname"), "title": row.get("title"),
                "desc": row.get("desc")}, cats, w)
    try:
        play = int(row.get("play") or 0)
    except Exception:
        play = 0
    if play >= 1000000:
        s += 2
    elif play >= 100000:
        s += 1
    return s


def _pushed_load():
    d = jload(PUSHED, {})
    if not isinstance(d, dict):
        d = {}
    return d


def _pushed_save(d):
    for k in sorted(d.keys())[:-7]:          # 只留最近 7 天,别越长越大
        d.pop(k, None)
    jsave(PUSHED, d)


def share_to_qq(rows, cats, w):
    """刷完挑 1~2 条发给泥泥。发不出去只打印一句,不影响刷视频本身。"""
    if not rows:
        return []
    try:
        from config import load
        c = load()
    except Exception:
        return []
    if not c.on("刷到好的发QQ"):
        return []
    try:
        most = int(str(c.get("每次最多发几条") or 1).strip())
    except Exception:
        most = 1
    most = max(1, min(3, most))

    done = _pushed_load()
    seen = set(done.get(today()) or [])
    cand = [(x, r) for x, r in
            ((_share_score(r, cats, w), r) for r in rows if r.get("bvid") and r["bvid"] not in seen)]
    cand = [r for s, r in sorted(cand, key=lambda x: -x[0]) if s > 0][:most]
    if not cand:
        print("这次没有够格单独发你的,只记下来")
        return []

    lines = ["刷到%s好看的,喵:" % ("个" if len(cand) == 1 else "几条")]
    for r in cand:
        lines.append("《%s》" % (r.get("title") or ""))
        lines.append("%s · %s播放" % (r.get("up") or "?", _fmt_play(r.get("play")) or "?"))
        lines.append("https://www.bilibili.com/video/%s" % r["bvid"])
        lines.append("")
    text = "\n".join(lines).strip()

    try:
        sys.path.insert(0, os.path.join(ROOT, "core", "qq"))
        from push import push_text
        ok, info = push_text(text)
    except Exception as e:                    # 没装 websockets / 找不到 push.py 之类
        print("发QQ出错:%r" % (e,))
        return []
    print(("发到QQ:" + info) if ok else ("没发出去:" + info))
    if ok:
        seen.update(r["bvid"] for r in cand)
        done[today()] = sorted(seen)
        _pushed_save(done)
        return cand
    return []


# ---------- watch ----------

def cmd_watch(n=None):
    if n is None:                     # 不传就按 设置.txt 的「刷视频条数」(11-01 泥泥要 20 条)
        try:
            from config import load
            n = max(1, min(50, int(str(load().get("刷视频条数") or 20).strip())))
        except Exception:
            n = 5
    b = Bili()
    cats, w = _prefs()
    try:
        # 11-01 13:5x:接口一次最多给 20 条。要 20 条候选就多要两轮,按 bvid 去重。
        items, seen_bv = [], set()
        for idx in (1, 2, 3):
            got = b.rcmd(ps=20, fresh_idx=idx) or []
            for it in got:
                bv = it.get("bvid")
                if bv and it.get("title") and bv not in seen_bv:
                    seen_bv.add(bv)
                    items.append(it)
            if len(items) >= n * 2:           # 备选够挑了就别再要
                break
    except BiliError as e:
        # 11-01:接口抽风时别再甩 traceback 到面板上,说人话就够
        print("拿不到推荐视频:%s" % e)
        print("（可能是 B站 接口改了，或者网断了。过一会儿再试）")
        return None
    # 推荐流里会混进直播、广告、活动卡片,它们没有 bvid/title —— 挑视频时滤掉,
    # 否则面板上会出现「·  | None | None 播放」这种空行。
    items = [it for it in items if it.get("bvid") and it.get("title")]
    if not items:
        print("推荐流这次一条视频都没给（都是广告/直播），过一会儿再刷")
        return None
    if cats and items:
        items = sorted(items, key=lambda it: -_score(it, cats, w))
        print("按偏好排:%s" % " / ".join("%s×%g" % (c, w.get(c, 1.0)) for c in cats))
    picked = items[:n]
    rows = []
    for it in picked:
        bv = it.get("bvid")
        row = {"bvid": bv, "title": it.get("title"), "up": (it.get("owner") or {}).get("name"),
               "play": (it.get("stat") or {}).get("view"), "tname": it.get("tname"),
               "desc": (it.get("desc") or "")[:200], "note": ""}
        try:
            v = b.view(bv)
            cid = v.get("cid")
            row["desc"] = (v.get("desc") or row["desc"])[:300]
            if cid:
                lines, lan = b.subtitle(bv, cid)
                if lines:
                    row["subtitle_lines"] = len(lines)
                    row["subtitle_head"] = [x[2] for x in lines[:20] if x[2]]
        except BiliError as e:
            row["note"] = str(e)[:120]
        rows.append(row)
        print("·", row["title"], "|", row["up"], "|", row["play"], "播放")
    p = os.path.join(WATCH, today() + ".json")
    old = jload(p, {"date": today(), "items": []})
    old["items"].extend(rows)
    jsave(p, old)
    print("记到", p, "共", len(old["items"]), "条")
    try:
        share_to_qq(rows, cats, w)
    except Exception as e:                    # 这一步再怎么出岔子也不许拦着刷视频
        print("分享到QQ这一步出错(不影响刷视频):%r" % (e,))
    try:
        cmd_c6(rows)
    except Exception as e:                    # 点赞/评论更不许拦着刷视频
        print("C6 点赞评论这一步出错(不影响刷视频):%r" % (e,))
    return rows


# ---------- C6:大号点赞 + 评论区 @主人(11-01 12:3x) ----------
#
# 泥泥原话:「刷到了同类的大肥鱼视频,你觉得喜欢,那你首先会点赞这条视频,拿你自己的
# 这个大肥鱼账号,然后会在评论区艾特主人的账号,然后说『这条视频我帮你记下了』。」
# 后来又补:这不是他自己用的,客户也要用 —— 所以号、条数、偏好全走 设置.txt:
#   B站自动点赞评论 = 是/否      总开关,默认否
#   B站白名单 = UID,UID         评论里 @ 的主人;空 = 不 @(只点赞)
#   B站每天最多 = 0~20           一天做几条,0 = 不做
# 同一条视频永远只做一次(记在 data/c6_done.json)。

C6_DONE = os.path.join(DATA, "c6_done.json")
C6_QUEUE = os.path.join(DATA, "c6_queue.json")   # 排队的:每半小时放一条出去

C6_SYS = """你在B站刷视频。现在要替你的主人,在一个视频下面留一句话,
意思是「这条我帮你记下了」。规则:
- 中文,一句话,25 个字以内,像朋友随口说的
- 必须提到视频里具体讲了什么(从标题、简介、字幕里抓一个点),不要空泛
- 不要表情符号,不要引号,不要「家人们」「啊啊啊」这类网络腔
- 不提高兴、喜欢、收藏、点赞这类字眼,不提你自己在刷视频
- 只输出这一句话本身,不要解释,不要分行"""


def _c6_done_load():
    d = jload(C6_DONE, {})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("items", {})
    return d


def _c6_done_save(d):
    # 90 天以前的老账不再留着;做过就是做过,不会因为删记录就重做(删的是死记录)
    cut = time.strftime("%Y-%m-%d", time.localtime(time.time() - 90 * 86400))
    it = d.get("items") or {}
    for k in [k for k, v in it.items() if str((v or {}).get("at") or "")[:10] < cut]:
        it.pop(k, None)
    jsave(C6_DONE, d)


# ---- 排队(11-01 13:42 泥泥定的:一条视频隔半个小时)----
#
# 20 条候选不是一口气做完,而是先进 data/c6_queue.json,每条一个到点时间;
# 调度器每分钟叫一次 cmd_c6tick,到点才做一条。软件一开就刷、就排队。

def _c6_queue_load():
    d = jload(C6_QUEUE, {})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("items", [])
    # 昨天没做完的不补(第二天重新刷一轮,不拿隔夜视频凑数)
    d["items"] = [x for x in d["items"] if str(x.get("due") or "")[:10] == today()]
    return d


def _c6_queue_save(d):
    jsave(C6_QUEUE, d)


def _c6_pending(d):
    return [x for x in d.get("items") or [] if not x.get("done")]


def _c6_gap(c):
    """每条之间隔多少分钟。设置.txt: B站评论间隔分钟,默认 30。"""
    try:
        n = int(str(c.get("B站评论间隔分钟") or 30).strip())
    except Exception:
        n = 30
    return max(5, min(180, n))


def _c6_owners(c, b):
    """设置里的 B站白名单 → [(uid, 昵称)]。昵称查不到就跳过(光写 UID 通知不到人)。"""
    raw = str(c.get("B站白名单") or "")
    out = []
    for part in re.split(r"[,，、;；\s]+", raw):
        part = part.strip()
        if not part:
            continue
        uid, _sep, name = part.partition("=")
        uid = re.sub(r"\D", "", uid)
        if not uid:
            continue
        name = name.strip() or b.uname_of(uid)
        out.append((uid, name))
    return out


_C6_END = "。！？!?～~…"


def _c6_ok(t):
    """这句到底是不是一句完整的话。11-01 13:10 真机踩过:模型被额度截断,
    发出去了半句「@泥泥酱NiniJam 闪翼双」。宁可只点赞,也不发半句。
    13:15 又发现反过来会误杀:模型常写完不带句号(「…我帮你记下了」),不能只认标点。"""
    t = (t or "").strip()
    if not t:
        return False
    if t[-1] in "，,、：:；;（(【[《「":
        return False                     # 断在半路上
    if t[-1] in _C6_END:
        return True
    return len(t) >= 6


def _c6_line(row, owner_name, hot=None):
    """让模型按视频内容现写一句;写不出来返回空(不拿模板凑)。"""
    head = []
    if row.get("title"):
        head.append("标题:" + str(row["title"]))
    if row.get("up"):
        head.append("UP主:" + str(row["up"]))
    if row.get("tname"):
        head.append("分区:" + str(row["tname"]))
    if row.get("desc"):
        head.append("简介:" + str(row["desc"])[:300])
    sub = row.get("subtitle_head") or []
    if sub:
        head.append("字幕开头:" + " ".join(str(x) for x in sub[:8])[:300])
    if hot:
        head.append("评论区在聊:" + " / ".join(str(x) for x in hot[:4])[:300])
    text = ""
    try:
        sys.path.insert(0, os.path.join(ROOT, "core"))
        from ai import 问
        # 11-01 13:0x:配的模型带思维链,偶尔额度全花在 reasoning 上、content 空。
        # 11-01 13:10:真机发出去半句「闪翼双」—— 额度给足 + 没写完就重来,三次都不完整就当没写。
        for _try in range(3):
            raw = 问(C6_SYS, "\n".join(head), max_tokens=1500, temperature=1.0)
            raw = (raw or "").strip().strip("「」\"'“”")
            raw = re.sub(r"\s+", " ", raw.split("\n")[0]).strip()
            if _c6_ok(raw):
                text = raw
                break
            if raw:
                print("模型这句没写完(第 %d 次):%r" % (_try + 1, raw[:60]))
    except Exception as e:
        print("叫模型写评论出错:%r" % (e,))
    text = (text or "").strip().strip("「」\"'“”")
    text = re.sub(r"\s+", " ", text.split("\n")[0]).strip()
    if len(text) > 40:
        text = text[:40].rstrip("，。、 ") + "…"
    if not text:
        return "", ""                     # 写不出来就不评论(不用模板凑,重复模板容易被判刷屏)
    if owner_name:
        return "@%s %s" % (owner_name, text), text
    return text, text


def cmd_c6(rows=None, dry=False):
    """刷完视频顺手排个队:挑「同类」→ 记进 c6_queue.json,到点再一条条做。

    dry=True 只打印打算干什么,一个请求都不发。返回这次排进队的条数。
    """
    try:
        from config import load
        c = load()
    except Exception as e:
        print("读不了设置:%r" % (e,))
        return 0
    if not c.on("B站自动点赞评论") and not dry:
        print("这一步关着(设置.txt:B站自动点赞评论 = 否),这次不点赞不评论")
        return 0
    most = max(0, min(20, int(str(c.get("B站每天最多") or 0).strip() or 0)))
    if most <= 0 and not dry:
        print("B站每天最多 是 0 条,这次不做")
        return 0

    if rows is None:                                  # 单独跑:用今天刷过的那份
        p = os.path.join(WATCH, today() + ".json")
        rows = jload(p, {}).get("items") or []
    if not rows:
        print("今天还没有刷到视频,先刷一轮再说")
        return 0

    b = Bili()
    if not b.logged_in:
        print("没登录 B站,先扫码登录再来")
        return 0
    cats, w = _prefs()
    owners = _c6_owners(c, b)
    if not owners:
        print("提醒:B站白名单 是空的 —— 只会点赞,评论里不 @ 任何人")

    d = _c6_done_load()
    it = d["items"]
    did_today = sum(1 for v in it.values()
                    if str((v or {}).get("at") or "")[:10] == today())
    q = _c6_queue_load()
    wait = _c6_pending(q)
    left = max(0, (most if most else 3) - did_today - len(wait))
    if left <= 0:
        print("今天已经做满或排满(做完 %d 条,在排 %d 条),不再加" % (did_today, len(wait)))
        return 0

    cand = []
    for r in rows:
        bv = r.get("bvid")
        if not bv or bv in it or any(x.get("bvid") == bv for x in q["items"]):
            continue
        s = _share_score(r, cats, w)
        if s <= 0:
            continue                                  # 不是「同类」,不碰
        cand.append((s, r))
    cand = [r for _s, r in sorted(cand, key=lambda x: -x[0])][:left]
    if not cand:
        print("这次没有够「同类」的,什么都不做")
        return 0

    gap = _c6_gap(c)
    base = time.time()
    if wait:                                   # 队里还有没做完的,新的接在后头,不插队
        try:
            base = max(base, max(time.mktime(time.strptime(str(x.get("due")), "%Y-%m-%d %H:%M"))
                                 for x in wait) + gap * 60)
        except Exception:
            pass
    planned = []
    for i, r in enumerate(cand):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(base + i * gap * 60))
        planned.append({"bvid": r["bvid"], "title": str(r.get("title") or "")[:80],
                        "due": when, "done": False})
        print("排上 %s 《%s》" % (when[11:], str(r.get("title") or "")[:40]))
    if dry:
        print("[dry] 只是排队,一条都没发")
        return 0
    q["items"].extend(planned)
    _c6_queue_save(q)
    print("排了 %d 条,每 %d 分钟一条(到点由调度器一条一条做)" % (len(planned), gap))
    return len(planned)


def cmd_c6tick():
    """调度器每分钟叫一次:队里到点了,就做一条(点赞 + 评论 + @主人)。

    一次只做一条 —— 这就是泥泥要的「一条视频隔半个小时」。
    返回 1 = 做了一条,0 = 没到点/没什么可做。
    """
    try:
        from config import load
        c = load()
    except Exception as e:
        print("读不了设置:%r" % (e,))
        return 0
    if not c.on("B站自动点赞评论"):
        return 0
    most = max(0, min(20, int(str(c.get("B站每天最多") or 0).strip() or 0)))
    if most <= 0:
        return 0

    q = _c6_queue_load()
    pend = _c6_pending(q)
    if not pend:
        return 0
    now = time.time()
    due = []
    for x in pend:
        try:
            t = time.mktime(time.strptime(str(x.get("due") or ""), "%Y-%m-%d %H:%M"))
        except Exception:
            t = 0
        if t <= now:
            due.append((t, x))
    if not due:
        return 0
    due.sort(key=lambda x: x[0])
    item = due[0][1]

    d = _c6_done_load()
    it = d["items"]
    did_today = sum(1 for v in it.values()
                    if str((v or {}).get("at") or "")[:10] == today())
    bv = str(item.get("bvid") or "")
    title = str(item.get("title") or bv)
    if did_today >= most:
        item["done"] = True
        _c6_queue_save(q)
        print("今天做满 %d 条了,这条不做了" % did_today)
        return 0
    if not bv or bv in it:
        item["done"] = True
        _c6_queue_save(q)
        print("这条以前做过,跳过:", title)
        return 0

    b = Bili()
    if not b.logged_in:
        print("没登录 B站,先扫码登录再来")
        return 0
    owners = _c6_owners(c, b)
    owner_name = owners[0][1] if owners else ""

    rows = jload(os.path.join(WATCH, today() + ".json"), {}).get("items") or []
    row = next((x for x in rows if x.get("bvid") == bv), None) or {"bvid": bv, "title": title}

    hot = []
    try:                                      # 评论区在聊什么,给模型当佐料(只读)
        for x in b.comments(b.aid_of(bv), ps=10)[:10]:
            msg = str(((x or {}).get("content") or {}).get("message") or "")
            msg = re.sub(r"\s+", " ", msg).strip()
            if msg:
                hot.append(msg[:60])
    except Exception as e:
        print("读评论区出错(不影响点赞评论):%r" % (e,))

    text, _line = _c6_line(row, owner_name, hot)
    ok_like, info_like = b.like(bv, True)
    print(("点赞成功:" if ok_like else "点赞没成(%s):" % info_like) + title)
    ok_reply, info_reply = (False, "")
    if ok_like and text:
        ok_reply, info_reply = b.reply(bv, text)
        print(("评论成功:" if ok_reply else "评论没成(%s):" % info_reply) + text)
    elif ok_like:
        # 模型没写出话就只点赞 —— 宁可少一条,也不拿同一句模板反复刷(大号容易被判定刷屏)
        info_reply = "没写出评论,只点了赞"
        print("只点赞,不评论:", title)
    else:
        info_reply = "点赞没成,评论也不发"

    if not (ok_like or ok_reply):
        print("这条没做成,留在队里下一分钟再试:", title)
        return 0

    it[bv] = {"at": time.strftime("%Y-%m-%d %H:%M"), "title": title[:80],
              "liked": bool(ok_like), "commented": bool(ok_reply),
              "text": text if ok_reply else ""}
    item["done"] = True
    _c6_done_save(d)
    _c6_queue_save(q)
    # 汇报说人话(11-01 20:43 泥泥定的):像随口说一句,不摆表格,也不报队里还剩几条
    who = owner_name or "泥泥酱"
    if ok_reply:
        word = "我刚才刷到一个好看的《%s》,给他点赞了,还评论了 @%s,我帮你记下了" % (title[:40], who)
    else:
        word = "我刚才刷到一个好看的《%s》,给他点赞了,评论没写出来,就先只点了个赞" % (title[:40],)
    try:
        sys.path.insert(0, os.path.join(ROOT, "core", "qq"))
        from push import push_text
        push_text(word)
    except Exception as e:
        print("发QQ出错(不影响已经做完的)：%r" % (e,))
    return 1


# ---------- stats ----------

def cmd_stats():
    b = Bili()
    mid = b.cookie.get("DedeUserID")
    d = b.get_wbi("https://api.bilibili.com/x/space/wbi/arc/search",
                  {"mid": mid, "pn": 1, "ps": 20, "order": "pubdate", "platform": "web"})
    vl = ((d or {}).get("list") or {}).get("vlist") or []
    ensure()
    new = not os.path.exists(STATS)
    一行 = {"日期": today(), "稿件": {}}
    with io.open(STATS, "a", encoding="utf-8", newline="") as f:
        wr = csv.writer(f)
        if new:
            wr.writerow(["date", "bvid", "title", "play", "comment", "like", "danmaku"])
        for x in vl:
            try:
                v = b.view(x.get("bvid"))
                st = v.get("stat") or {}
            except BiliError:
                continue
            wr.writerow([today(), x.get("bvid"), x.get("title"),
                         st.get("view"), st.get("reply"), st.get("like"), st.get("danmaku")])
            一行["稿件"][x.get("bvid")] = {
                "标题": x.get("title"), "播放": st.get("view"),
                "评论": st.get("reply"), "点赞": st.get("like"), "弹幕": st.get("danmaku")}
            print("·", x.get("title"), st.get("view"), "播放", st.get("like"), "赞", st.get("reply"), "评")
    print("记到", STATS)

    m = mem()
    if m and 一行["稿件"]:
        m.记数据(一行)
        for bv, s in 一行["稿件"].items():
            m.记视频(bv, 标题=s.get("标题"))
        m.生成()
        print("记忆也记了")


# ---------- tidy ----------

def cmd_tidy():
    p = os.path.join(WATCH, today() + ".json")
    d = jload(p, {"items": []})
    titles = [x.get("title") for x in d.get("items", [])][:5]
    text = "今天刷了 %d 条:%s" % (len(titles), " / ".join(t or "" for t in titles))
    m = mem()
    if m:
        m.记(text, 来源="B站", 标签="刷推荐", 类别="B站")
        m.生成()
    print("写进记忆:", text[:120])


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "watch":
        if cmd_watch(int(argv[1]) if len(argv) > 1 else None) is None:
            return 1
    elif cmd == "stats":
        cmd_stats()
    elif cmd == "c6":
        cmd_c6(dry=("--dry" in argv))
    elif cmd == "c6tick":
        cmd_c6tick()
    elif cmd == "c6q":
        q = _c6_queue_load()
        pend = _c6_pending(q)
        if not q["items"]:
            print("队是空的")
        for x in q["items"]:
            print("%s  %s  《%s》" % ("做完" if x.get("done") else "待做",
                                     x.get("due"), str(x.get("title") or "")[:40]))
        print("在排 %d 条" % len(pend))
    elif cmd == "tidy":
        cmd_tidy()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
