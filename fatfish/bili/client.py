# -*- coding: utf-8 -*-
"""B站 接口客户端(零第三方依赖,只用标准库)。

设计原则:
  * 全部走 HTTP,不碰浏览器、不碰鼠标键盘 —— 这就是「不抢电脑」的意思。
  * cookie 存在 data/bili_cookie.json,由扫码登录写入,过期就重新扫。
  * 只读接口(推荐流 / 视频信息 / 字幕 / 评论)不需要登录也能用;
    写接口(评论 / 点赞 / 投稿)才需要 cookie + w_rid 签名。

用法:
    from client import Bili
    b = Bili()
    print(b.nav())
    for v in b.rcmd(): print(v['title'])
"""

import hashlib
import io
import json
import os
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
COOKIE_FILE = os.path.join(DATA, "bili_cookie.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0")

# wbi 签名用的重排表(B站 前端硬编码)
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


class BiliError(Exception):
    pass


def _mixin_key(orig):
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _enc_wbi(params, img_key, sub_key):
    mixin = _mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = int(time.time())
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    params = dict(sorted(params.items()))
    query = urllib.parse.urlencode(params)
    params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return params


class Bili:
    def __init__(self, cookie_file=COOKIE_FILE):
        self.cookie_file = cookie_file
        self.cookie = self._load_cookie()
        self._img_key = None
        self._sub_key = None
        self.last_error = None

    # ---------- cookie ----------

    def _load_cookie(self):
        try:
            with io.open(self.cookie_file, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and "cookie" in d:
                d = d["cookie"]
            if isinstance(d, list):  # [{name,value}, ...]
                return {x["name"]: x["value"] for x in d if "name" in x}
            if isinstance(d, dict):
                return {k: str(v) for k, v in d.items() if isinstance(v, (str, int))}
        except Exception:
            pass
        return {}

    def save_cookie(self, cookie):
        os.makedirs(DATA, exist_ok=True)
        with io.open(self.cookie_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(cookie, ensure_ascii=False, indent=2))

    @property
    def logged_in(self):
        return bool(self.cookie.get("SESSDATA"))

    # ---------- http ----------

    def _headers(self):
        h = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.bilibili.com",
        }
        if self.cookie:
            h["Cookie"] = "; ".join("%s=%s" % (k, v) for k, v in self.cookie.items())
        return h

    def get(self, url, params=None, timeout=15, raw=False):
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
        except Exception as e:
            self.last_error = repr(e)
            raise BiliError("请求失败: %s (%s)" % (url, e))
        if raw:
            return body
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            raise BiliError("返回不是 JSON: %s" % url)
        if d.get("code") not in (0, None):
            self.last_error = "%s %s" % (d.get("code"), d.get("message"))
            raise BiliError("接口返回 %s: %s" % (d.get("code"), d.get("message")))
        return d.get("data") or d.get("result")

    # ---------- wbi ----------

    def _keys(self):
        if self._img_key and self._sub_key:
            return self._img_key, self._sub_key
        d = self.get("https://api.bilibili.com/x/web-interface/nav")
        wbi = (d or {}).get("wbi_img") or {}
        img = os.path.basename(wbi.get("img_url", "")).split(".")[0]
        sub = os.path.basename(wbi.get("sub_url", "")).split(".")[0]
        if not img or not sub:
            raise BiliError("拿不到 wbi key")
        self._img_key, self._sub_key = img, sub
        return img, sub

    def get_wbi(self, url, params, timeout=15):
        img, sub = self._keys()
        return self.get(url, _enc_wbi(params, img, sub), timeout=timeout)

    # ---------- 读接口 ----------

    def nav(self):
        """登录状态。返回 dict,里面 isLogin / uname / mid / money。"""
        return self.get("https://api.bilibili.com/x/web-interface/nav") or {}

    def rcmd(self, ps=12, fresh_idx=1):
        """首页推荐流。

        11-01 07:0x:老的 /index/top/rcmd 一律回 -400 —— 泥泥点「刷推荐视频」
        没反应就是栽在这儿。新地址是 /wbi/index/top/feed/rcmd,要 wbi 签名;
        万一哪天它也挂了,退到 /popular 热门(那个不挑,永远有东西)。
        """
        params = {
            "web_location": "1430650", "feed_version": "V8", "fresh_type": 4,
            "fresh_idx": fresh_idx, "fresh_idx_1h": fresh_idx,
            "homepage_ver": 1, "brush": 0, "scrl": 0, "last_y": 0, "y_num": 5,
            "ps": max(1, min(30, ps)),        # 11-01 13:5x: ps 给太大(80)B站 直接 -400
        }
        try:
            d = self.get_wbi("https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd",
                             params) or {}
            items = d.get("item") or []
            if items:
                return items
        except BiliError as e:
            self.last_error = str(e)
        d = self.get("https://api.bilibili.com/x/web-interface/popular",
                     {"ps": max(1, min(20, ps)), "pn": 1}) or {}
        return d.get("list") or []

    def view(self, bvid):
        """视频信息:标题、简介、分P(cid)、UP 主、播放数。"""
        return self.get("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid}) or {}

    def player(self, bvid, cid):
        """播放器信息,含字幕列表。"""
        return self.get_wbi("https://api.bilibili.com/x/player/wbi/v2",
                            {"bvid": bvid, "cid": cid}) or {}

    def subtitle(self, bvid, cid):
        """字幕正文(有的话)。返回 [(from, to, text), ...]。"""
        p = self.player(bvid, cid)
        subs = ((p.get("subtitle") or {}).get("subtitles")) or []
        if not subs:
            return [], None
        # 优先中文
        pick = None
        for s in subs:
            if s.get("lan", "").startswith("zh"):
                pick = s
                break
        pick = pick or subs[0]
        url = pick.get("subtitle_url") or ""
        if url.startswith("//"):
            url = "https:" + url
        if not url:
            return [], pick.get("lan")
        body = self.get(url, raw=True, timeout=20)
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            return [], pick.get("lan")
        lines = [(x.get("from"), x.get("to"), x.get("content")) for x in (d.get("body") or [])]
        return lines, pick.get("lan")

    def comments(self, oid, pn=1, ps=20, sort=2):
        """评论区。oid 是 aid。sort=2 按热度。"""
        d = self.get("https://api.bilibili.com/x/v2/reply",
                     {"type": 1, "oid": oid, "pn": pn, "ps": ps, "sort": sort}) or {}
        return d.get("replies") or []

    def aid_of(self, bvid):
        return self.view(bvid).get("aid")

    # ---------- 写接口(C6:点赞、评论。11-01 12:3x) ----------
    # 只读接口出错就 raise BiliError;写接口不一样 —— 业务上的「已点赞」「太频繁」
    # 都要原样交给上层判断,所以返回 (code, message),code=0 才算成。

    def post(self, url, data, timeout=15):
        """带 cookie + csrf 的表单 POST。返回 (code, message)。网络不通返回 (-1, 原因)。"""
        data = dict(data or {})
        data["csrf"] = self.cookie.get("bili_jct", "")
        data.setdefault("csrf_token", data["csrf"])
        body = urllib.parse.urlencode(data).encode("utf-8")
        h = self._headers()
        h["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except Exception as e:
            self.last_error = repr(e)
            return -1, "请求失败:%s" % (e,)
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return -2, "返回不是 JSON"
        code = d.get("code")
        if code in (0, None):
            return 0, ""
        self.last_error = "%s %s" % (code, d.get("message"))
        return int(code), str(d.get("message") or "")

    def like(self, bvid, on=True):
        """给视频点赞(on=False 是取消)。返回 (ok, 说明)。"""
        if not self.logged_in:
            return False, "没登录"
        if not self.cookie.get("bili_jct"):
            return False, "cookie 里没有 bili_jct,要重新扫码登录"
        try:
            aid = self.aid_of(bvid)
        except BiliError as e:
            return False, "拿不到 aid:%s" % e
        code, msg = self.post("https://api.bilibili.com/x/web-interface/archive/like",
                              {"aid": aid, "like": 1 if on else 0})
        if code == 0:
            return True, "点赞成功" if on else "取消点赞成功"
        if code == 65006:
            return True, "之前已经赞过"
        if code in (-101, -111):
            return False, "登录状态失效了(cookie 过期),要重新扫码"
        if code == 10003:
            return False, "视频不存在或已删除"
        return False, "点赞失败:%s %s" % (code, msg)

    def reply(self, bvid, text):
        """在视频下发一条评论。返回 (ok, 说明);成功时说明里带 rpid。"""
        if not self.logged_in:
            return False, "没登录"
        if not self.cookie.get("bili_jct"):
            return False, "cookie 里没有 bili_jct,要重新扫码登录"
        text = (text or "").strip()
        if not text:
            return False, "评论是空的"
        try:
            aid = self.aid_of(bvid)
        except BiliError as e:
            return False, "拿不到 aid:%s" % e
        code, msg = self.post("https://api.bilibili.com/x/v2/reply/add",
                              {"oid": aid, "type": 1, "message": text, "plat": 1,
                               "ordering": "heat"})
        if code == 0:
            return True, "评论发出去了"
        if code in (-101, -111):
            return False, "登录状态失效了(cookie 过期),要重新扫码"
        if code == 12051:
            return False, "评论太频繁了(被限流),这条先跳过"
        if code == 12002:
            return False, "这个视频关了评论区"
        if code == -400:
            return False, "评论内容被拒了(可能带了敏感词或被判定广告)"
        if code == 12061:
            return False, "评论内容不合规,这条先跳过"
        return False, "评论失败:%s %s" % (code, msg)

    def uname_of(self, mid):
        """按 UID 查昵称(评论里 @ 要用昵称,光有 UID 通知不到人)。查不到返回 ""。"""
        try:
            d = self.get("https://api.bilibili.com/x/web-interface/card",
                         {"mid": mid, "photo": "false"}) or {}
            return str((d.get("card") or {}).get("name") or "")
        except BiliError:
            return ""
