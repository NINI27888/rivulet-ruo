# -*- coding: utf-8 -*-
"""若的工作区 · 对话编排。

一轮对话:存用户话 → 问模型 → 可能要调工具 → 拿工具结果再问 → 存回复。
模型用 Provider 抽象,默认是 EchoProvider(不联网、不花钱),真模型以后接。

用法::

    chat = ChatService(store, registry, provider=EchoProvider())
    out = chat.send("今天我在家做了什么")
"""
from __future__ import annotations

import json
import time

MAX_TOOL_ROUNDS = 3


class EchoProvider:
    """不联网的演示/验收用 provider。规则简单但可预测。"""

    name = "echo"
    model = "echo-1"

    INTENTS = ("记忆", "做了什么", "做过", "在家", "昨天", "今天", "田里", "小窝")

    def respond(self, messages: list[dict], tools: list[dict], *, session_id: str | None = None) -> dict:
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            return {"type": "text", "text": self._summarize(last.get("content") or "{}")}
        text = str(last.get("content") or "")
        if any(k in text for k in self.INTENTS) and any(t["name"] == "memory.search" for t in tools):
            since = None
            if "今天" in text:
                since = time.strftime("%Y-%m-%d 00:00:00")
            return {"type": "tool", "name": "memory.search",
                    "args": {"q": "", "since": since, "limit": 10}}
        return {"type": "text", "text": "在的。这是工作区的演示回复(还没接真模型)。"}

    @staticmethod
    def _summarize(raw: str) -> str:
        try:
            data = json.loads(raw)
        except Exception:
            return "工具返回了看不懂的内容。"
        items = data.get("items") or []
        if not items:
            return "统一记忆里这段时间没有记录。"
        lines = ["查到 %d 条记录:" % int(data.get("total") or len(items))]
        for it in items[:8]:
            lines.append("- [%s] (%s) %s" % ((it.get("at") or "")[11:16], it.get("source"), it.get("content")))
        return "\n".join(lines)


class ChatService:
    def __init__(self, store, registry, provider=None):
        self.store = store
        self.registry = registry
        self.provider = provider or EchoProvider()
        self.history: dict[str, list[dict]] = {}
        self._force_new_conv = False

    def reset_context(self):
        """人格换了:丢掉旧上下文,下一句话开新会话,别让旧口气串过来。"""
        self.history.clear()
        self._force_new_conv = True

    def ensure_conversation(self, conversation_id: str | None, text: str) -> str:
        if self._force_new_conv:
            self._force_new_conv = False
            conv = self.store.create_conversation(title="(换人格后)" + text[:20])
            return conv["id"]
        if conversation_id:
            return conversation_id
        conv = self.store.create_conversation(title=text[:24])
        return conv["id"]

    def send(self, text: str, conversation_id: str | None = None) -> dict:
        cid = self.ensure_conversation(conversation_id, text)
        task = self.store.add_task("对话: " + text[:30], cid)
        prior = self.history.get(cid)
        if prior is None:
            prior = [{"role": m["role"], "content": m["content"]}
                     for m in self.store.messages(cid, 20) if m["role"] in ("user", "assistant")]
        self.store.add_message(cid, "user", text)
        msgs = list(prior)
        msgs.append({"role": "user", "content": text})
        tool_calls = []
        reply = ""
        try:
            for _ in range(MAX_TOOL_ROUNDS + 1):
                out = self.provider.respond(msgs, self.registry.specs(), session_id=cid)
                if out.get("type") == "tool":
                    name = out.get("name") or ""
                    args = out.get("args") or {}
                    try:
                        result = self.registry.run(name, args)
                        tool_calls.append({"name": name, "args": args, "ok": True})
                    except Exception:
                        result = {"error": "此功能暂时不可用。"}
                        tool_calls.append({"name": name, "ok": False})
                    payload = json.dumps(result, ensure_ascii=False)
                    self.store.add_message(cid, "tool", payload, {"tool": name})
                    call = out.get("call")
                    if call:
                        msgs.append({"role": "assistant", "content": None, "tool_calls": [call]})
                    tool_msg = {"role": "tool", "content": payload}
                    if call:
                        tool_msg["tool_call_id"] = call["id"]
                    msgs.append(tool_msg)
                    continue
                reply = str(out.get("text") or "")
                break
            else:
                reply = "工具调用轮次过多,先停一下。"
        except Exception as e:
            self.store.finish_task(task["id"], ok=False, result=str(e)[:200])
            self.history[cid] = [m for m in msgs if m["role"] in ("user", "assistant")
                                 and not m.get("tool_calls")][-20:]
            return {"ok": False, "conversation_id": cid, "error": str(e)}
        else:
            self.store.finish_task(task["id"], ok=True, result=reply[:200])

        self.store.add_message(cid, "assistant", reply)
        msgs.append({"role": "assistant", "content": reply})
        self.history[cid] = [m for m in msgs if m["role"] in ("user", "assistant")
                             and not m.get("tool_calls")][-20:]
        return {"ok": True, "conversation_id": cid, "reply": reply,
                "tool_calls": tool_calls, "task_id": task["id"]}
