-- 002 鉴权 / 节点 / 命令 扩展
ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user';
ALTER TABLE users ADD COLUMN last_login TEXT;
ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0;

ALTER TABLE nodes ADD COLUMN token TEXT;
ALTER TABLE nodes ADD COLUMN status TEXT NOT NULL DEFAULT 'offline';
ALTER TABLE nodes ADD COLUMN ip TEXT;
ALTER TABLE nodes ADD COLUMN meta TEXT;
ALTER TABLE nodes ADD COLUMN user_id INTEGER;
ALTER TABLE nodes ADD COLUMN last_ip TEXT;

ALTER TABLE commands ADD COLUMN created_by INTEGER;
ALTER TABLE commands ADD COLUMN delivered_at TEXT;
ALTER TABLE commands ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL,
  last_used  TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  actor      TEXT,
  action     TEXT NOT NULL,
  target     TEXT,
  note       TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_commands_node_status ON commands(node_id, status);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_token ON nodes(token);
