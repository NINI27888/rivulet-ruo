# -*- coding: utf-8 -*-
r"""通过已连接的 OneBot 会话发布 QQ 空间说说。"""
import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request

from workspace.provider_llm import NoRedirect

OPENER = urllib.request.build_opener(NoRedirect())
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


async def _session(onebot_action):
    result = await onebot_action("get_cookies", {"domain": "qzone.qq.com"})
    data = result.get("data") if result and result.get("retcode") == 0 else None
    ck = _parse(data.get("cookies") if isinstance(data, dict) else data)
    key = ck.get("p_skey") or ck.get("skey") or ""
    uin = (ck.get("uin") or ck.get("p_uin") or "").lstrip("o")
    if not key or not uin.isdecimal():
        raise RuntimeError("QQ 空间登录状态不可用")
    return ck, uin, _bkn(key)


def _request_body(url, form, headers):
    data = urllib.parse.urlencode(form).encode("ascii")
    request = urllib.request.Request(url, data=data, headers=headers)
    with OPENER.open(request, timeout=25) as response:
        return response.read(128 * 1024).decode("utf-8", "replace").strip()


async def publish(text, onebot_action):
    """发一条说说,返回 (成功?, 说明)。"""
    text = (text or "").strip()
    if not text:
        return False, "内容是空的"
    if len(text) > 900:
        text = text[:900]
    try:
        ck, uin, gtk = await _session(onebot_action)
    except (RuntimeError, OSError, ValueError, TypeError):
        return False, "暂时无法确认 QQ 空间登录，请重连 QQ 后重试。"
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
        body = await asyncio.to_thread(_request_body, url, form, headers)
    except urllib.error.HTTPError as error:
        return False, f"QQ 空间接口暂时不可用（HTTP {error.code}）。"
    except (OSError, ValueError):
        return False, "无法确认 QQ 空间是否发布，请先在空间查看。"
    # 返回是 JSONP:frameElement.callback({...})
    payload = body
    i, j = payload.find("{"), payload.rfind("}")
    if i >= 0 and j > i:
        payload = payload[i:j + 1]
    try:
        d = json.loads(payload)
    except ValueError:
        return False, "QQ 空间没有确认发布结果，请先在空间查看。"
    if isinstance(d, dict):
        code = d.get("code")
        if code == 0 or (code is None and (d.get("createTime") or d.get("tid") or d.get("feedid"))):
            return True, "说说已发布。"
        if isinstance(code, int):
            return False, f"QQ 空间拒绝发布（{code}）。"
    return False, "QQ 空间没有确认发布结果，请先在空间查看。"
