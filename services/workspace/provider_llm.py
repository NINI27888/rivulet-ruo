"""Send requests to the selected OpenAI-compatible model."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from workspace.models import PROVIDERS


DEFAULT_BASES = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
GO_PATHS = {"/inference/go/openai/v1", "/inference/go/v1", "/zen/go/v1"}


class ModelError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(NoRedirect())


def _base(provider: str, value: str) -> str:
    base = str(value or DEFAULT_BASES.get(provider, "")).strip().rstrip("/")
    return base.removesuffix("/chat/completions").rstrip("/")


def model_settings(body: dict, existing: dict | None = None) -> dict:
    provider = str(body.get("provider") or "custom").lower()
    model = str(body.get("model") or "").strip()
    if provider not in PROVIDERS:
        raise ModelError("请选择一个模型服务商。")
    base = _base(provider, body.get("base_url"))
    try:
        url = urlsplit(base)
        if (url.scheme not in ("https", "http") or not url.hostname or url.username is not None
                or url.query or url.fragment or any(c.isspace() for c in base)):
            raise ValueError
        if url.port == 0:
            raise ValueError
    except ValueError:
        raise ModelError("请填写服务商提供的完整接口地址，以 https:// 或 http:// 开头。") from None
    if not model or any(c.isspace() for c in model):
        raise ModelError("请复制服务商控制台里的模型标识，模型名不能含空格。")
    key = str(body.get("api_key") or "").strip()
    if not key and existing:
        old = _base(str(existing.get("provider") or "custom"), existing.get("base_url"))
        if old == base:
            key = str(existing.get("api_key") or "").strip()
    if not key and url.hostname not in LOCAL_HOSTS:
        raise ModelError("请从所选服务商的控制台复制 API 密钥。")
    if any(c.isspace() for c in key):
        raise ModelError("API 密钥里有空格或换行，请重新完整复制。")
    return {"provider": provider, "base_url": base, "model": model, "api_key": key}


def complete_message(settings: dict, messages: list[dict], *, max_tokens: int = 900,
                     temperature: float = 0.7, timeout: float = 45, tools=None,
                     session_id: str | None = None) -> dict:
    base, model, key = (settings[k] for k in ("base_url", "model", "api_key"))
    url = urlsplit(base)
    host = url.hostname
    go = (url.scheme == "https" and host == "opencode.ai" and url.port in (None, 443)
          and url.path in GO_PATHS)
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
               "temperature": temperature}
    if host == "api.openai.com" and model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        payload.pop("temperature")
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    if (host == "api.deepseek.com" or go) and model.startswith(("deepseek-flash", "deepseek-v4")):
        payload["thinking"] = {"type": "disabled"}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json"}
    if go:
        session = uuid5(NAMESPACE_URL, "rivulet:" + session_id) if session_id else uuid4()
        headers.update({
            "User-Agent": "Rivulet/0.10.2",
            "X-OpenCode-Session": "ses_" + session.hex,
        })
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base + "/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers=headers)
    try:
        with _opener.open(req, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        tips = {
            400: "服务商拒绝了请求，请确认这个模型支持聊天接口。",
            401: "这个平台没有接受这份密钥。请打开所选平台的密钥页，重新复制完整密钥。",
            402: "模型账户余额不足，请到服务商控制台查看。",
            403: "API 密钥没有该模型的使用权限，请检查服务商控制台。",
            404: "没找到接口或模型。请核对接口地址和模型名。",
            429: "请求过于频繁或额度已用完，请稍后再试或检查账户额度。",
        }
        if go:
            tips[401] = "OpenCode Go 没有接受这份密钥。请在开通 Go 的 OpenCode 工作区复制完整密钥。"
            tips[403] = "OpenCode Go 暂时无法使用这个模型。请检查 Go 订阅，并在工作区隐私设置中确认已允许 Global（全球）区域。"
        if 300 <= e.code < 400:
            tip = "接口要求跳转，请填写服务商提供的最终接口地址。"
        else:
            tip = tips.get(e.code, "服务商暂时无法回答，请稍后再试。")
        raise ModelError(f"{tip}（HTTP {e.code}）") from None
    except (urllib.error.URLError, OSError):
        raise ModelError("连接模型服务超时或失败，请检查接口地址和网络后重试。") from None
    except ValueError:
        raise ModelError("这个地址的返回格式不支持聊天，请核对接口地址。") from None
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ModelError("模型服务没有返回可用的回答，请检查接口与模型。") from None
    if not isinstance(message, dict):
        raise ModelError("模型服务没有返回可用的回答，请检查接口与模型。")
    content = message.get("content") or ""
    if isinstance(content, str) and content.strip():
        return {**message, "content": content.strip()}
    if tools and message.get("tool_calls"):
        return message
    raise ModelError("模型已连接，但没有返回文字。请换一个聊天模型再试。")


def probe_model(settings: dict) -> None:
    complete_message(settings, [{"role": "user", "content": "请只回复 OK。"}],
                     max_tokens=128, timeout=18)


def _tool_name(name: str) -> str:
    return "".join(c if (c.isascii() and c.isalnum()) or c in "_-" else "_" for c in name)


class LLMProvider:
    name = "llm"

    def __init__(self, models):
        self.models = models
        self.persona_api = (os.environ.get("RIVULET_PERSONA") or "http://127.0.0.1:18098").rstrip("/")

    def persona_brief(self) -> str:
        try:
            with urllib.request.urlopen(self.persona_api + "/api/persona/brief?channel=workspace",
                                        timeout=3) as response:
                return (json.load(response) or {}).get("brief") or ""
        except (OSError, ValueError):
            return ""

    def require_model(self) -> dict:
        active = self.models.active()
        if not active:
            raise ModelError("请先点「配置聊天模型」，测试通过后就可以对话。")
        return model_settings(active)

    def status(self) -> dict:
        active = self.models.active()
        if not active:
            return {"ok": True, "enabled": False}
        result = {k: active[k] for k in ("id", "name", "provider", "model", "base_url")}
        try:
            result["base_url"] = model_settings(active)["base_url"]
            result["enabled"] = True
        except ModelError as e:
            result.update(enabled=False, hint=str(e))
        return {"ok": True, "has_key": bool(active["api_key"]), **result}

    def respond(self, messages: list[dict], tools: list[dict], *, session_id: str | None = None) -> dict:
        model = self.require_model()
        if not messages or messages[0].get("role") != "system":
            persona = self.persona_brief()
            if persona:
                messages = [{"role": "system", "content": persona}] + messages
        names = {}
        definitions = []
        for tool in tools:
            name = _tool_name(tool["name"])
            names[name] = tool["name"]
            props = {k: {"type": "string", "description": v} for k, v in tool["params"].items()}
            definitions.append({"type": "function", "function": {
                "name": name, "description": tool["description"],
                "parameters": {"type": "object", "properties": props},
            }})
        message = complete_message(model, messages, tools=definitions, session_id=session_id)
        calls = message.get("tool_calls") or []
        if calls:
            call = calls[0]
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            name = fn.get("name") or ""
            return {"type": "tool", "name": names.get(name, name), "args": args, "call": call}
        return {"type": "text", "text": message["content"]}
