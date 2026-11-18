-- 003_obs.sql  可观测性事件表(只新增,不碰旧 7 张表:零 ALTER、零 DROP)
--
-- 执行方式(现状机制):
--   db.init() 按文件名排序 executescript() 本文件,成功后往 schema_migrations 插一行 '003_obs.sql'。
--
-- 失败恢复(重要):
--   sqlite3 的 executescript() 会先隐式 COMMIT 再往下跑,所以本文件若中途失败,
--   可能出现「表建了、索引没建全、schema_migrations 没记录」。因为每条语句都带 IF NOT EXISTS,
--   直接重启重跑即可 —— 已建的对象跳过、缺的补上,最后照样写记录。
--   若表本身都没建全且重跑报错,从 fatfish.db.bak_<ts> 整库还原后再排查。
--
-- 回滚:
--   数据可弃:DROP TABLE obs_events; + DELETE FROM schema_migrations WHERE name='003_obs.sql'; → 重启。
--   想留数据只停用:不动表,把 config.json 的 features.obs 设 false(唯一开关)。
--
-- 验收判据:
--   PRAGMA table_info(obs_events) 与本文件一致;PRAGMA index_list(obs_events) 有 4 个索引;
--   旧 7 张表的 PRAGMA table_info 与 3-0-1 快照 diff 为空。
--
-- 写入策略(obs.py):只写 error(5xx/未捕获)、http_error(4xx)、slow(≥slow_ms 的 2xx/3xx);
-- 保留 keep_days=30 / keep_rows=5000,启动 prune 一次、之后每写 200 行 prune 一次,只删本表。

CREATE TABLE IF NOT EXISTS obs_events (
  id          INTEGER PRIMARY KEY,
  ts          TEXT    NOT NULL,              -- ISO UTC 毫秒:YYYY-MM-DDTHH:MM:SS.mmmZ
  kind        TEXT    NOT NULL,              -- error | http_error | slow
  rid         TEXT    NOT NULL,              -- 请求 ID,格式 req_+16hex
  method      TEXT,
  path        TEXT,
  status      INTEGER,
  dur_ms      INTEGER,
  uid         INTEGER,
  username    TEXT,
  ip          TEXT,
  exc_type    TEXT,
  exc_msg     TEXT,
  traceback   TEXT,
  extra_json  TEXT NOT NULL DEFAULT '{}',
  app_version TEXT
);

-- 按时间倒查 / prune 按天数删
CREATE INDEX IF NOT EXISTS ix_obs_ts      ON obs_events(ts);
-- 按类型 + 时间查(errors 接口的 kind 过滤)
CREATE INDEX IF NOT EXISTS ix_obs_kind_ts ON obs_events(kind, ts);
-- 按 rid 追一整条请求
CREATE INDEX IF NOT EXISTS ix_obs_rid     ON obs_events(rid);
-- 按路径 + 时间查(某个接口反复 4xx/慢)
CREATE INDEX IF NOT EXISTS ix_obs_path_ts ON obs_events(path, ts);
