# -*- coding: utf-8 -*-
"""若的工作区 · 工具注册表。

工具只读优先;每个工具声明名字、说明、参数,调用统一返回 dict。
工作区的对话层只认这个注册表,不直接写死某个模块。
"""
from __future__ import annotations


class Tool:
    name = ""
    description = ""
    params = {}

    def run(self, args: dict) -> dict:      # pragma: no cover - 子类实现
        raise NotImplementedError

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "params": self.params}


class MemorySearchTool(Tool):
    name = "memory.search"
    description = "在统一记忆库里按关键词/时间/来源搜索,返回带来源与时间的条目"
    params = {"q": "关键词,可空", "sources": "来源列表,可空", "since": "起始时间,可空",
              "until": "结束时间,可空", "limit": "条数,默认 10"}

    def __init__(self, client):
        self.client = client

    def run(self, args: dict) -> dict:
        res = self.client.search(
            str(args.get("q") or ""),
            sources=args.get("sources") or None,
            since=args.get("since") or None,
            until=args.get("until") or None,
            limit=int(args.get("limit") or 10),
        )
        items = res.get("items") or []
        return {"total": res.get("total", len(items)),
                "items": [{"at": it.get("occurred_at"), "source": it.get("source"),
                           "kind": it.get("kind"), "content": it.get("content")}
                          for it in items]}


class HomeStatusTool(Tool):
    name = "home.status"
    description = "查看鱼的小窝当前阶段、金币、角色与任务待办"
    params = {}

    def __init__(self, client):
        self.client = client

    def run(self, args: dict) -> dict:
        return self.client.status()


class StatusTool(Tool):
    name = "status.overview"
    description = "查看 Rivulet 各模块在线状态"
    params = {}

    def __init__(self, client):
        self.client = client

    def run(self, args: dict) -> dict:
        return self.client.overview()


class Registry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> "Registry":
        self._tools[tool.name] = tool
        return self

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    def has(self, name: str) -> bool:
        return name in self._tools

    def run(self, name: str, args: dict | None = None) -> dict:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError("没有这个工具: %s" % name)
        return tool.run(args or {})
