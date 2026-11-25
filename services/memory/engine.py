# -*- coding: utf-8 -*-
"""Rivulet 若 · 统一记忆库 v1(纯标准库 + SQLite)。

设计要点(对应 V1.1 第三节):
  * 每条记忆带 user_id / source / occurred_at / permission / context;
  * kind 区分 fact(真实记录)/ inference(推断)/ summary(模型总结),不许混;
  * 检索:关键词(FTS5 trigram,中文可按子串)、来源、类型、时间范围、权限;
  * 软删除;重复导入靠 (source, source_ref) 唯一索引挡住。

用法::

    from engine import MemoryEngine
    m = MemoryEngine("data/memory.db")
    m.add(source="qq", content="今天在家做了什么", occurred_at="2026-11-05 12:00")
    m.search("在家")
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id           TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL DEFAULT 'local',
  source       TEXT NOT NULL,
  source_ref   TEXT NOT NULL DEFAULT '',
  kind         TEXT NOT NULL DEFAULT 'fact',
  content      TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  occurred_at  TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  permission   TEXT NOT NULL DEFAULT 'private',
  scope        TEXT NOT NULL DEFAULT '',
  deleted_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mem_src_ref ON memories(source, source_ref)
  WHERE source_ref <> '';
CREATE INDEX IF NOT EXISTS ix_mem_user_time ON memories(user_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_mem_source      ON memories(source, occurred_at);
CREATE INDEX IF NOT EXISTS ix_mem_kind        ON memories(kind);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  content, tokenize='trigram', content='memories', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF content ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
  INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""

SOURCES = ("qq", "bili", "home", "farm", "workspace", "manual", "system")
KINDS = ("fact", "inference", "summary")
PERMISSIONS = ("private", "shared", "system")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _new_id() -> str:
    return "mem_" + uuid.uuid4().hex[:20]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


class MemoryEngine:
    # ---- 淡忘模型(和桌面端记忆树同一套数,改一处要改两处)----
    # 普通记忆 5 天掉一半分,置顶 20 天(慢 4 倍);每回想一次年龄 ×0.82(年轻 18%),最多 14 次。
    HALF_LIFE_DAYS = 5.0
    HALF_LIFE_PINNED = 20.0
    RECALL_KEEP = 0.82
    RECALL_CAP = 14
    RATING_CLEAR = 70.0     # >=70 清晰
    RATING_FUZZY = 42.0     # <42 模糊

    def __init__(self, path: str = "data/memory.db"):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._c = sqlite3.connect(self.path, timeout=10)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.executescript(SCHEMA)
        self._c.commit()

    # ---------------------------------------------------------------- 写

    def add(self, *, content: str, source: str = "manual", user_id: str = "local",
            kind: str = "fact", occurred_at: str | None = None,
            permission: str = "private", scope: str = "",
            source_ref: str = "", context: dict | None = None) -> dict:
        content = _clean(content)
        if not content:
            raise ValueError("content 不能为空")
        if source not in SOURCES:
            raise ValueError(f"source 不认识: {source}")
        if kind not in KINDS:
            raise ValueError(f"kind 不认识: {kind}")
        if permission not in PERMISSIONS:
            raise ValueError(f"permission 不认识: {permission}")
        if context is not None and not isinstance(context, dict):
            raise ValueError("context 必须是对象")

        mid = _new_id()
        now = _now()
        try:
            self._c.execute(
                "INSERT INTO memories(id,user_id,source,source_ref,kind,content,context_json,"
                "occurred_at,created_at,permission,scope) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (mid, str(user_id), source, str(source_ref), kind, content,
                 json.dumps(context or {}, ensure_ascii=False),
                 occurred_at or now, now, permission, scope))
            self._c.commit()
        except sqlite3.IntegrityError:
            row = self._c.execute(
                "SELECT id FROM memories WHERE source=? AND source_ref=?",
                (source, str(source_ref))).fetchone()
            return {"ok": True, "id": row["id"], "duplicate": True}
        return {"ok": True, "id": mid, "duplicate": False}

    def update(self, mid: str, **fields) -> dict:
        allowed = {"content", "kind", "permission", "scope", "occurred_at", "context"}
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"不能改: {k}")
            if k == "context":
                if v is not None and not isinstance(v, dict):
                    raise ValueError("context 必须是对象")
                sets.append("context_json=?")
                args.append(json.dumps(v or {}, ensure_ascii=False))
            else:
                sets.append(f"{k}=?")
                args.append(_clean(v) if k == "content" else v)
        if not sets:
            return {"ok": False, "error": "nothing to update"}
        args.append(mid)
        cur = self._c.execute(
            f"UPDATE memories SET {', '.join(sets)} WHERE id=? AND deleted_at IS NULL", args)
        self._c.commit()
        return {"ok": cur.rowcount == 1}

    def delete(self, mid: str) -> dict:
        cur = self._c.execute("UPDATE memories SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                              (_now(), mid))
        self._c.commit()
        return {"ok": cur.rowcount == 1}

    # ---------------------------------------------------------------- 读

    def get(self, mid: str) -> dict | None:
        row = self._c.execute("SELECT * FROM memories WHERE id=? AND deleted_at IS NULL",
                              (mid,)).fetchone()
        return self._row(row) if row else None

    def search(self, q: str = "", *, user_id: str | None = None,
               sources: list[str] | None = None, kinds: list[str] | None = None,
               permission: str | None = None,
               since: str | None = None, until: str | None = None,
               limit: int = 20, offset: int = 0) -> dict:
        where = ["deleted_at IS NULL"]
        args: list = []
        if user_id:
            where.append("user_id=?")
            args.append(user_id)
        if sources:
            where.append("source IN (%s)" % ",".join("?" * len(sources)))
            args.extend(sources)
        if kinds:
            where.append("kind IN (%s)" % ",".join("?" * len(kinds)))
            args.extend(kinds)
        if permission:
            where.append("permission=?")
            args.append(permission)
        if since:
            where.append("occurred_at>=?")
            args.append(since)
        if until:
            where.append("occurred_at<=?")
            args.append(until)

        q = _clean(q)
        if q:
            if len(q) >= 3:
                # trigram:FTS 走子串;短词(1~2 字)trigram 建不了索引,退 LIKE
                where.append("rowid IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)")
                args.append('"' + q.replace('"', '""') + '"')
            else:
                where.append("content LIKE ? ESCAPE '\\'")
                args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")

        sql_where = " AND ".join(where)
        total = self._c.execute(f"SELECT COUNT(*) n FROM memories WHERE {sql_where}", args).fetchone()["n"]
        rows = self._c.execute(
            f"SELECT * FROM memories WHERE {sql_where} ORDER BY occurred_at DESC, created_at DESC "
            f"LIMIT ? OFFSET ?", args + [max(1, min(int(limit), 2000)), max(0, int(offset))]).fetchall()
        return {"total": total, "items": [self._row(r) for r in rows]}

    def stats(self, *, user_id: str | None = None) -> dict:
        w, a = "deleted_at IS NULL", []
        if user_id:
            w += " AND user_id=?"
            a.append(user_id)
        by_source = {r["source"]: r["n"] for r in
                     self._c.execute(f"SELECT source, COUNT(*) n FROM memories WHERE {w} GROUP BY source", a)}
        by_kind = {r["kind"]: r["n"] for r in
                   self._c.execute(f"SELECT kind, COUNT(*) n FROM memories WHERE {w} GROUP BY kind", a)}
        total = self._c.execute(f"SELECT COUNT(*) n FROM memories WHERE {w}", a).fetchone()["n"]
        items = [self._row(r) for r in self._c.execute(f"SELECT * FROM memories WHERE {w}", a)]
        by_rating = {"clear": 0, "normal": 0, "fuzzy": 0}
        for it in items:
            by_rating[it["rating"]] = by_rating.get(it["rating"], 0) + 1
        avg = round(sum(it["score"] for it in items) / len(items), 1) if items else 0.0
        return {"total": total, "by_source": by_source, "by_kind": by_kind,
                "by_rating": by_rating, "avg_score": avg,
                "pinned": sum(1 for it in items if it["pinned"])}

    # ---------------------------------------------------------------- 导入旧记忆

    LEGACY_SOURCE = {"QQ": "qq", "B站": "bili", "田地": "farm", "小家": "home",
                     "手动": "manual", "面板": "workspace"}

    def import_legacy(self, path: str, *, user_id: str = "local") -> dict:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        notes = data.get("记事") or []
        added = dup = 0
        for n in notes:
            text = _clean(n.get("内容") or "")
            if not text:
                continue
            src_name = str(n.get("来源") or "手动")
            source = self.LEGACY_SOURCE.get(src_name, "manual")
            res = self.add(content=text, source=source, user_id=user_id, kind="fact",
                           occurred_at=str(n.get("时间") or "")[:19] or None,
                           permission="private", source_ref=str(n.get("id") or ""),
                           context={"legacy_category": n.get("类别"), "legacy_source": src_name,
                                    "tags": n.get("标签") or ""})
            added += 0 if res.get("duplicate") else 1
            dup += 1 if res.get("duplicate") else 0
        self._c.commit()
        return {"notes": len(notes), "added": added, "duplicate": dup}

    # ---------------------------------------------------------------- 内部

    @staticmethod
    def _parse_dt(s):
        """occurred_at 是本机时间字符串(YYYY-MM-DD HH:MM[:SS]),也兼容 ISO。"""
        if not s:
            return None
        t = str(s).strip().replace("T", " ")
        for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
            try:
                return datetime.strptime(t[:n], fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(t.replace("Z", ""))
        except Exception:
            return None

    @classmethod
    def score_of(cls, occurred_at, context: dict | None, mid: str = "") -> float:
        """分数:时间淡忘 + 回想带来的“变年轻”。0~100。"""
        ctx = context or {}
        dt = cls._parse_dt(occurred_at) or datetime.now()
        age = max(0.0, (datetime.now() - dt).total_seconds() / 86400.0)
        try:
            n = int(ctx.get("recall_count") or 0)
        except (TypeError, ValueError):
            n = 0
        n = min(cls.RECALL_CAP, max(0, n))
        age_eff = age * (cls.RECALL_KEEP ** n)
        hl = cls.HALF_LIFE_PINNED if ctx.get("pinned") else cls.HALF_LIFE_DAYS
        base = 100.0 * (2.0 ** (-age_eff / hl))
        # 确定性抖动(按 id),让同一批记忆不至于分数全一样;和前端同一个哈希
        h = 2166136261
        for ch in str(mid or ""):
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        jitter = ((h % 997) / 997.0) * 12.0 - 6.0
        return max(0.0, min(100.0, base + jitter))

    @classmethod
    def rating_of(cls, score: float) -> str:
        return ("clear" if score >= cls.RATING_CLEAR
                else ("normal" if score >= cls.RATING_FUZZY else "fuzzy"))

    @classmethod
    def _row(cls, r: sqlite3.Row) -> dict:
        d = dict(r)
        try:
            d["context"] = json.loads(d.pop("context_json") or "{}")
        except Exception:
            d["context"] = {}
            d.pop("context_json", None)
        if not isinstance(d["context"], dict):
            d["context"] = {}
        ctx = d["context"]
        d["score"] = round(cls.score_of(d.get("occurred_at"), ctx, d.get("id", "")), 1)
        d["rating"] = cls.rating_of(d["score"])
        d["pinned"] = bool(ctx.get("pinned"))
        try:
            d["recall_count"] = int(ctx.get("recall_count") or 0)
        except (TypeError, ValueError):
            d["recall_count"] = 0
        return d

    def close(self) -> None:
        self._c.close()


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="memory-engine")
    ap.add_argument("--db", default="data/memory.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add")
    p_add.add_argument("content")
    p_add.add_argument("--source", default="manual")
    p_add.add_argument("--kind", default="fact")
    p_add.add_argument("--at", default=None)
    p_q = sub.add_parser("search")
    p_q.add_argument("q")
    p_q.add_argument("--limit", type=int, default=10)
    sub.add_parser("stats")
    p_imp = sub.add_parser("import")
    p_imp.add_argument("path")
    a = ap.parse_args(argv)
    m = MemoryEngine(a.db)
    if a.cmd == "add":
        print(json.dumps(m.add(content=a.content, source=a.source, kind=a.kind,
                               occurred_at=a.at), ensure_ascii=False))
    elif a.cmd == "search":
        res = m.search(a.q, limit=a.limit)
        for it in res["items"]:
            print(f'[{it["occurred_at"]}] ({it["source"]}/{it["kind"]}) {it["content"]}')
        print(f'共 {res["total"]} 条')
    elif a.cmd == "stats":
        print(json.dumps(m.stats(), ensure_ascii=False, indent=2))
    elif a.cmd == "import":
        print(json.dumps(m.import_legacy(a.path), ensure_ascii=False))
    m.close()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
