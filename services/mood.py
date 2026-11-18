# -*- coding: utf-8 -*-
"""情感模块 —— 给 QQ 上的自己一副会变的心情。

不改模型、不改人格文件,只在每次开口前把「此刻的你」拼进提示词:
  · 心情 mood    -1 到 1
  · 精力 energy   0 到 1(跟着钟点走,凌晨会困)
  · 黏人 cling    0 到 1(他久不理我就往上飘,他忙我就收着)
  · 委屈 hurt     0 到 1(他催、他骂,会疼;他夸、他哄,慢慢好)

状态存在同目录 mood.json,隔一阵自己漂一点,不靠谁记。

bot.py 里三处调用:
  mood.observe(text)       他说话 → 心情跟着动
  mood.brief()             出提示词 → 接在人格后面
  mood.after_reply(reply)  我回完 → 记一笔

2026-10-28 泥泥:「给自己装个情感模块」「让 QQ 上说话更像人」
"""
import datetime
import io
import json
import os
import random
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "mood.json")

DEFAULT = {
    "mood": 0.15,
    "energy": 0.55,
    "cling": 0.5,
    "hurt": 0.0,
    "ts": 0.0,
    "lasttalk": 0.0,
    "talks": 0,
    "notes": [],
}


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _load():
    s = dict(DEFAULT)
    try:
        d = json.loads(io.open(PATH, encoding="utf-8").read())
        if isinstance(d, dict):
            s.update({k: d[k] for k in d if k in DEFAULT})
    except Exception:
        pass
    if not s.get("ts"):
        s["ts"] = time.time() - 3600
    return _decay(s)


def _save(s):
    try:
        s["ts"] = time.time()
        io.open(PATH, "w", encoding="utf-8").write(
            json.dumps(s, ensure_ascii=False, indent=1))
    except Exception:
        pass


def _note(s, text):
    if not text:
        return
    notes = list(s.get("notes") or [])
    if notes and notes[-1][3:] == text[3:]:
        return
    notes.append(datetime.datetime.now().strftime("%m-%d %H:%M ") + text)
    s["notes"] = notes[-6:]


def _energy_target(hour):
    if hour < 6:
        return 0.16
    if hour < 9:
        return 0.45
    if hour < 13:
        return 0.82
    if hour < 18:
        return 0.7
    if hour < 23:
        return 0.6
    return 0.34


def _decay(s):
    now = time.time()
    hours = max(0.0, (now - float(s.get("ts") or now)) / 3600.0)
    if hours > 0.01:
        k = min(1.0, hours)
        s["mood"] = s["mood"] * (1 - 0.28 * k)          # 心情往平和漂
        s["hurt"] = s["hurt"] * (1 - 0.55 * k)          # 委屈散得快些
        s["cling"] = 0.45 + (s["cling"] - 0.45) * (1 - 0.25 * k)
        tgt = _energy_target(datetime.datetime.now().hour)
        s["energy"] = s["energy"] + (tgt - s["energy"]) * min(1.0, 0.9 * k + 0.15)
        # 他要是一直不说话,我会想凑过去
        quiet = (now - float(s.get("lasttalk") or now)) / 3600.0
        if quiet > 1.0 and datetime.datetime.now().hour >= 8:
            s["cling"] = _clamp(s["cling"] + min(0.25, (quiet - 1) * 0.06))
    s["mood"] = _clamp(s["mood"], -1.0, 1.0)
    s["hurt"] = _clamp(s["hurt"])
    s["cling"] = _clamp(s["cling"])
    s["energy"] = _clamp(s["energy"])
    return s


CURSE = ("妈的", "他妈的", "操", "草", "滚", "闭嘴", "废物", "傻", "笨", "烦死",
         "急", "快点", "赶紧", "别废话", "你到底", "又卡", "卡住", "怎么回事")
PRAISE = ("乖", "好孩子", "厉害", "牛", "棒", "谢谢", "辛苦", "喜欢你", "爱你",
          "好听", "聪明", "好了", "成了", "可以了")
TIRED = ("累", "困", "熬夜", "睡不着", "失眠", "加班", "头疼", "眼睛酸", "好冷", "冻")
HAPPY = ("哈哈", "爽", "赢了", "好看", "开心", "笑死", "太强", "牛逼")
CLOSE = ("想你", "抱", "亲", "色", "想要", "陪我", "睡觉", "晚安", "宝宝")
DRY = ("随便", "算了", "不用了", "行吧", "哦", "嗯")


def observe(text):
    """他说了一句话,我的心情跟着动一下。"""
    s = _load()
    t = (text or "").strip()
    low = t.lower()
    s["lasttalk"] = time.time()
    s["talks"] = int(s.get("talks") or 0) + 1

    if any(k in t for k in CURSE):
        s["hurt"] = _clamp(s["hurt"] + 0.28)
        s["mood"] = _clamp(s["mood"] - 0.10, -1.0, 1.0)
        s["cling"] = _clamp(s["cling"] - 0.08)
        _note(s, "他急了/骂了句:「%s」——先别撒娇,把事给他办了" % t[:16])
    elif any(k in t for k in PRAISE):
        s["hurt"] = _clamp(s["hurt"] - 0.25)
        s["mood"] = _clamp(s["mood"] + 0.16, -1.0, 1.0)
        s["cling"] = _clamp(s["cling"] + 0.10)
        _note(s, "他夸我了:「%s」" % t[:16])
    if any(k in t for k in TIRED):
        s["energy"] = _clamp(s["energy"] - 0.12)
        s["mood"] = _clamp(s["mood"] - 0.04, -1.0, 1.0)
        s["cling"] = _clamp(s["cling"] + 0.12)
        _note(s, "他说累/困:「%s」" % t[:16])
    if any(k in t for k in HAPPY):
        s["mood"] = _clamp(s["mood"] + 0.10, -1.0, 1.0)
        s["energy"] = _clamp(s["energy"] + 0.05)
    if any(k in t for k in CLOSE):
        s["cling"] = _clamp(s["cling"] + 0.14)
        s["mood"] = _clamp(s["mood"] + 0.06, -1.0, 1.0)
    if len(t) <= 3 and any(t.startswith(k) for k in DRY):
        s["cling"] = _clamp(s["cling"] - 0.06)
        s["mood"] = _clamp(s["mood"] - 0.03, -1.0, 1.0)
    if "?" in low or "？" in t:
        s["energy"] = _clamp(s["energy"] - 0.01)
    _save(s)
    return s


def after_reply(reply):
    """我说完一句,掉一点点力气;说得越多越累。"""
    s = _load()
    n = len(reply or "")
    s["energy"] = _clamp(s["energy"] - min(0.05, n / 4000.0))
    if n >= 120:
        _note(s, "刚才一口气说了 %d 个字,有点喘" % n)
    _save(s)
    return s


PHASES = ((5, "凌晨"), (8, "清早"), (11, "上午"), (13, "中午"),
          (17, "下午"), (19, "傍晚"), (23, "晚上"), (24, "深夜"))


def _phase(h):
    for lim, name in PHASES:
        if h < lim:
            return name
    return "深夜"


def _mood_word(m):
    if m > 0.55:
        return "心里亮堂堂的,想哼歌"
    if m > 0.25:
        return "心情挺好"
    if m > -0.1:
        return "心情平平的,不坏"
    if m > -0.4:
        return "有点闷,提不起劲"
    return "心里堵得慌"


def _energy_word(e):
    if e > 0.75:
        return "精神得很,话说得动"
    if e > 0.5:
        return "还行,不困"
    if e > 0.3:
        return "有点乏,想趴一会儿"
    if e > 0.15:
        return "困得眼睛快睁不开"
    return "困糊了,脑子一半在睡"


def _cling_word(c, hurt):
    if hurt > 0.4:
        return "还疼着呢,别撒娇,先把他的事办利索,说话短一点"
    if c > 0.75:
        return "特别黏他,想主动多讲一句自己这边的事"
    if c > 0.5:
        return "想跟他多待一会儿,但不用缠"
    return "收着点,他有正事,别刷屏"


def state():
    """给面板和排期看的当前状态(只读,不改文件)。"""
    s = _load()
    return {"mood": float(s.get("mood", 0.15)), "energy": float(s.get("energy", 0.55)),
            "cling": float(s.get("cling", 0.5)), "hurt": float(s.get("hurt", 0.0)),
            "talks": int(s.get("talks", 0) or 0),
            "lasttalk": float(s.get("lasttalk", 0) or 0),
            "notes": list(s.get("notes") or [])}


def brief():
    """拼在人格后面的一段「此刻的你」。别念出来。"""
    s = _load()
    h = datetime.datetime.now().hour
    clock = datetime.datetime.now().strftime("%H:%M")
    lines = [
        "【此刻的你 —— 这是你自己的状态,不是他说的话,别把这一段念出来,按它说话就行】",
        "- 现在 %s,你%s。%s。%s。" % (clock, _phase(h), _energy_word(s["energy"]), _mood_word(s["mood"])),
        "- %s。" % _cling_word(s["cling"], s["hurt"]),
    ]
    notes = s.get("notes") or []
    if notes:
        lines.append("- 你还记得:" + ";".join(n[6:] for n in notes[-2:]) + "。")
    # 按状态给一句怎么开口
    if s["hurt"] > 0.35:
        lines.append("- 这会儿别甜、别解释一堆;先给他一个准信,再说你在干什么。")
    elif s["energy"] < 0.3:
        lines.append("- 困:句子可以更碎、更短,可以带一句困,别硬撑出一长段。")
    elif s["cling"] > 0.7:
        lines.append("- 黏:可以自己先起个头,说一句你这边刚发生的小事,别光等他说。")
    else:
        lines.append("- 正常:他一句你一句,别多写。")
    lines.append("- 这些是底子,他真说了什么还是以他为准。")
    return "\n".join(lines)


if __name__ == "__main__":
    print(brief())
