# Rivulet 若

一个陪在你身边的 **AI 桌面伙伴**。她能陪你聊天、回 QQ、投 B 站，
还会在「鱼的小窝」里过自己的日子。

> 目前仍在开发状态，核心功能已跑通，剩下主要是改 UI 和验收。
> **计划开源，不用于任何商业目的。**

## 下载

**只要一个 exe，双击即用**（Windows 64 位，不用装 Python / Rust / 任何依赖）：

去 **[Releases](../../releases)** 下载 `Rivulet若-v0.10.1-win64.exe`（25.6 MB）。

- 放到桌面或 D 盘（**不要放系统盘的程序目录**，那里没有写权限）
- 双击运行；首次启动会自动解压运行环境（约 10 秒），之后启动很快
- 弹「Windows 已保护你的电脑」→ 更多信息 → 仍要运行（未做数字签名）

首次运行会先落到「一键部署」引导页，三步走完（或点跳过）就进首页。

## 这是什么

| 板块 | 说明 |
|---|---|
| 一键部署 | 扫码 / 填模型 Key，三步走完即可使用；新用户首次进入会先引导到这里 |
| 工作区 | 沉浸式对话 |
| QQ Agent | 自动回复、白名单、最近对话、空间说说、主动消息、审计日志 |
| B站 Agent | 投稿、稿件管理、评论回复、每日推荐 |
| 记忆树 | 记忆检索、筛选、详情、模糊记忆 |
| 鱼的小窝 | 纯本地生活引擎：小家、田地、工坊、雇佣、账本、日记（无联网） |
| 心情 / 人格 | 全局共用的状态与人格设定 |
| 节点 / 命令 / 账号 / 设置 | 管理与可观测性 |

## 架构

```
ui/            前端(静态 HTML/CSS/JS,经 Tauri WebView 加载)
src-tauri/     桌面壳(Rust):登录态 DPAPI 保管、IPC 转发、鱼的小窝引擎
core/          本地 Core 服务(FastAPI):账号 / 权限 / 节点 / 命令 / 可观测性
services/      本地功能服务(Python 标准库 http.server):QQ / B站 / 人格 / 心情 / 部署
```

- 账号与权限在 `core`：自助注册只能建普通用户，管理接口需 admin。
- 鱼的小窝是 **Rust 内置引擎**（`src-tauri/src/home.rs`），状态存本地 `data/home.json`，不联网。
- 各服务端口可用环境变量覆盖（如 `RIVULET_QQ_PORT`、`RIVULET_SETUP_PORT`）。

## 本地运行

1. 用 `core/config.example.json` 复制出 `core/config.json`，按需改 `db_path` / `port`
2. 启动 Core：`python core/main.py`
3. 按需启动 `services/` 下各服务（或由桌面端自启）
4. 桌面端：`cd src-tauri && cargo tauri dev`

依赖：Python 3.12（fastapi / uvicorn / httpx / websockets）、Rust 1.77+、Tauri 2。

## 隐私说明

本仓库**不含**任何运行时数据、人格设定、对话记录、模型密钥或登录凭证。
`data/`、`*.db`、`session.bin`、`qq.json`、`bili.json`、`persona.json` 等均在
`.gitignore` 中。首次运行会自行生成空数据。

`core/config.example.json` 是脱敏后的配置模板，只有相对路径，无密钥。

## 贡献者

创作者 **泥泥**，核心贡献者 **啵啵小C**。感谢一路把它做出来的每一个人。
详见 [CONTRIBUTORS.md](CONTRIBUTORS.md)。

## 许可

开源、**非商业**（署名 / 非商业 / 相同方式共享）。详见 [LICENSE](LICENSE)。

## 状态

开发中 · v0.10.1
