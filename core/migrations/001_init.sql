-- 001 初始表:账号、节点、命令、任务
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  pass_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  hostname TEXT,
  platform TEXT,
  app_version TEXT,
  last_seen TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  node_id TEXT,
  kind TEXT NOT NULL,
  payload TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  result TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  cron TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
