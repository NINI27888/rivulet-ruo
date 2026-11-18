# -*- coding: utf-8 -*-
r"""发 QQ 空间说说:借 NapCat 的 get_cookies 拿登录态,走空间网页版发布接口。

用法(在 bot 里):
    import qzone
    ok, msg = await qzone.publish("今天也很忙")
"""
import json
import time

import httpx
import websockets

WS = "ws://127.0.0.1:3001"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _bkn(skey):
    h = 5381
    for c in skey:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF


def _parse(c):
    if isinstance(c, dict):
        return dict(c)
    d = {}
    for part in str(c or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            d[k.strip()] = v.strip()
    return d


async def _call(action, params, echo, timeout=15):
    async with websockets.connect(WS, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"action": action, "params": params, "echo": echo},
                                 ensure_ascii=False))
        end = time.time() + timeout
        while time.time() < end:
            try:
                raw = await ws.recv()
            except Exception:
                return {}
            try:
                d = json.loads(raw)
            except Exception:
                continue
            if d.get("echo") == echo:
                return d
    return {}


async def _session():
    r = await _call("get_cookies", {"domain": "qzone.qq.com"}, "qz-ck")
    data = r.get("data")
    ck = _parse(data.get("cookies") if isinstance(data, dict) else data)
    key = ck.get("p_skey") or ck.get("skey") or ""
    uin = (ck.get("uin") or ck.get("p_uin") or "").lstrip("o")
    if not key or not uin:
        raise RuntimeError("拿不到空间登录态(p_skey/uin 为空)")
    return ck, uin, _bkn(key)


async def publish(text):
    """发一条说说,返回 (成功?, 说明)。"""
    text = (text or "").strip()
    if not text:
        return False, "内容是空的"
    if len(text) > 900:
        text = text[:900]
    try:
        ck, uin, gtk = await _session()
    except Exception as e:
        return False, "登录态失败: %r" % (e,)
    cookie_hdr = "; ".join("%s=%s" % (k, v) for k, v in ck.items())
    url = ("https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/"
           "cgi-bin/emotion_cgi_publish_v6?g_tk=%d" % gtk)
    form = {
        "syn_tweet_verson": "1", "paramstr": "1", "who": "1",
        "con": text, "feedversion": "1", "ver": "1", "ugc_right": "1",
        "to_sign": "0", "hostuin": uin, "code": "1", "format": "json",
        "qzreferrer": "https://user.qzone.qq.com/%s" % uin,
    }
    headers = {
        "Cookie": cookie_hdr,
        "Referer": "https://user.qzone.qq.com/%s" % uin,
        "Origin": "https://user.qzone.qq.com",
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as cli:
            resp = await cli.post(url, data=form, headers=headers)
    except Exception as e:
        return False, "请求失败: %r" % (e,)
    body = (resp.text or "").strip()
    # 返回是 JSONP:frameElement.callback({...})
    payload = body
    i, j = payload.find("{"), payload.rfind("}")
    if i >= 0 and j > i:
        payload = payload[i:j + 1]
    try:
        d = json.loads(payload)
    except Exception:
        # 10-26 23:32:真发出去了,但返回的 JSONP 有时不是合法 JSON(带控制字符),
        # json.loads 挂掉会被当成失败,下一次到点又发一遍。里面有 createTime/tid 就算成。
        if '"createTime"' in body or '"tid"' in body:
            return True, "发出去了(返回不是合法 JSON,但里面有 createTime/tid)"
        try:  # 10-27 晚:留下来看原文,别再瞎猜
            Path(r"D:\qqbot\qzone_last_error.txt").write_text(body[:4000], encoding="utf-8", errors="replace")
        except Exception:
            pass
        return False, "返回不是 json(%d 字节): %s" % (len(body), body[:200])
    if d.get("createTime") or d.get("tid") or d.get("feedid"):
        return True, "发出去了 feedid=%s" % (d.get("tid") or d.get("feedid") or "?")
    if d.get("code") == 0:
        return True, "发出了(接口 code=0)"
    return False, "接口拒绝: %s" % json.dumps(d, ensure_ascii=False)[:300]
