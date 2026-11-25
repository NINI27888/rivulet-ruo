# GitHub 上传准备清单

**状态**：已准备完毕，**未执行 push**（外部写操作留给你确认）。
**范围**：仅「若」板块（`rivulet-app`），不涉及 `项目-AI平台` 等其他仓库。

---

## 一、为什么不能直接提交现有仓库

当前 `git` 仓库根是上一层的整个 workspace 目录（不在 `rivulet-app` 内），
不是 `rivulet-app`：

- `rivulet-app/` 目录**一个文件都没被 git 跟踪**（`git ls-files | grep rivulet-app` = 0）
- 仓库里混着别的项目的改动（fatfish / qq-bot / 数据库 / 缓存）
- **没有配置远程仓库**（`git remote -v` 为空），目前无法 push
- 整个目录数 GB（src-tauri 4.1G、promo 729M、target-fix 1.9G、work 417M）

所以正确做法是：**用 `github-release/` 这个干净目录单独建仓**，不要 `git add .`。

## 二、已准备好的发布目录

`github-release/`：87 个文件 / 6.9 MB，只含源码、文档、模板与必要资源。

```
github-release/
├── README.md                 项目说明(不含私人内容)
├── GITHUB_准备清单.md         本文件
├── .gitignore                严格忽略规则
├── check_privacy.py          提交前脱敏自检(退出码0=干净)
├── ui/                       前端 12 个 js/html/css + assets/
├── src-tauri/                Rust 桌面壳(Cargo.toml / src / capabilities / icons)
├── core/                     FastAPI Core 源码 + migrations + config.example.json
├── services/                 Python 本地服务源码
└── docs/                     UI 规范文档
```

## 三、脱敏结果（自检退出码 0）

| 检查 | 结果 |
|---|---|
| 私人数据文件（`persona.json` / `home.json` / `*.db` / `*.jsonl` / `session.bin` / cookie） | ✅ 未混入 |
| 模型密钥 / 长密钥串 / `sk-` 开头真实 Key | ✅ 未发现 |
| 本机绝对路径（盘符 / 用户目录） | ✅ 已清理 |
| 运行时配置 `qq.json` / `bili.json` / `config.json` | ✅ 不在发布目录 |
| `core/config.example.json` | ✅ 仅相对路径，无密钥 |

**已处理的 3 处**：`services/qq_service.py` 原本硬编码了你本机的
本机绝对路径（NapCat 启动器与 headless 目录），在**发布目录副本**中
改成了 `os.environ.get("RIVULET_NAPCAT_DIR", ...)` 等可移植写法。
> 注意：**你本机 `rivulet-app/services/qq_service.py` 没有改**，仍保持原样可正常运行。
> 两处不一致是有意为之，后续同步时留意。

## 四、署名与许可（已按授权定稿）

- **创作者**：泥泥 —— 公开保留在 `src-tauri/Cargo.toml` 的 `authors`、
  `LICENSE`、`README.md`、`CONTRIBUTORS.md`
- **核心贡献者**：啵啵小C —— 写入 `LICENSE`、`CONTRIBUTORS.md`、`README.md`、
  `Cargo.toml` 注释
- **LICENSE**：已生成，开源 **非商业**（署名 / 非商业 / 相同方式共享），
  含免责声明与"开发中"状态说明
- 产品内置默认人格模板文案为代码常量，属产品默认设定，保留

## 五、已执行

- `git init` → 脱敏自检（退出码 0）→ `git add`（逐项审查）→ `git commit`
- 4 份 `routes_auth.py` 同源缺陷同步修复
- **已推送到 GitHub**：https://github.com/NINI27888/rivulet-ruo
  - 分支 `main`，提交 `f6e50d6`，72 文件 / 29225 行
  - 已核对远程：根目录 11 项 + `ui/` 13 项齐全，**无 `data/`、`*.db`、`session.bin`**

> 说明：GitHub 连接器未授予建仓权限（403），仓库由账号本人在网页创建为空仓库后，
> 通过本地 git（Git Credential Manager）完成推送。

## 六、后续可选

- admin 初始账号：生产 Core 不含 `/api/auth/register`，如需可补一份部署运维文档
  说明如何用管理接口初始化首个 admin
- 日常更新流程：改完代码后 `python check_privacy.py` → `git add -A` → `git commit`
  → `git push`

---

## 八、发布 Windows 便携包（v0.10.1）

**产物**：`Rivulet若-v0.10.1-win64.zip`（22.8 MB，解压 72 MB，含自带 Python runtime）

**做了什么**
- 用最新代码重新构建 exe（`cargo build --release`，版本 0.10.1）
- 便携化改造 `lib.rs`：Python 优先用 `runtime\python.exe`；`PYTHONPATH` 指向
  自带 `site-packages`（不再硬编码本机路径）；`services.json` 的 cwd 支持相对路径；
  **补上 core 服务自启**（8088，以前没启动，导致新机器登录失败）
- `qzone.py` 改软依赖：缺 `httpx`/`websockets` 时只有「发说说」不可用，
  整个 QQ 服务不会崩
- **清空全部运行时数据**：`data/`、`core/data/`、`services/data/`、`*.db`、
  `*.jsonl`、`session.bin`、`persona.json`、`qq.json`、`bili.json`、`*.log`
- 默认人格模板去私人化：「泥泥」「宝宝」等称呼改为中性的「使用者」
- 实测：包内 runtime 启动 core（9 张表 / 0 用户）+ 5 个功能服务，**5/5 通过**

**管理员仅本机登录（已实现并验证）**
- `login`：admin 角色 + 非本机来源 → 403「管理员账号只能在本机登录」
- `current_user`：admin token 在远端使用 → 403，并记 `admin.remote_blocked` 告警
- 实测：本机 admin 200；远程 admin 403；远程普通用户 200；admin token 外带 403

**发 Release 的步骤（在网页操作，连接器无 Release 写权限）**
1. 打开 https://github.com/NINI27888/rivulet-ruo/releases/new
2. Choose a tag → 选 `v0.10.1`（已推送）
3. Release title：`v0.10.1`
4. 描述里写明：Windows 64 位、解压双击即用、首次运行会被 SmartScreen 拦
5. 附件：把 `Rivulet若-v0.10.1-win64.zip` 拖进 "Attach binaries"
6. 点 **Publish release**

---

## 九、改为单文件 exe（v0.10.1 最终方案）

用户要求「一个 exe 下载就能用」，因此在 `lib.rs` 增加单文件模式：

- `src-tauri/assets/bundle.zip`（13.0 MB）打包 `runtime/` + `core/` + `services/` + `fatfish/` + 使用说明
- `static BUNDLE: &[u8] = include_bytes!("../assets/bundle.zip");`
- `ensure_bundle()`：启动时若 exe 同级没有 `runtime\python.exe`，把内嵌 zip 写到临时文件，
  用 Windows 自带 `tar -xf` 解压到 exe 同级（失败退回系统解压命令），再删临时文件
- 解压后 `ensure_services()` 正常拉起 core(8088) + 5 个功能服务

**产物对比**

| 文件 | 大小 | 说明 |
|---|---|---|
| `Rivulet若-v0.10.1-win64.exe` | **25.6 MB** | **推荐**：单文件，下载双击即用，首次自动解压 |
| `Rivulet若-v0.10.1-win64.zip` | 22.8 MB | 文件夹版，解压后双击 exe |

**实测**：空目录放置单文件 exe → 运行 → 自动解出 1035 个 runtime 文件（40 MB），
`data/home.json` 生成，`ensure_services` 被执行；用解压出的 runtime 启动 core
→ 9 张表 / 0 用户，自动拉起 5 个服务，全部正常。

---

## 十、单文件版的升级机制（重要,每次发版都依赖它）

`ensure_bundle()` 的规则:

1. **版本戳** = 内嵌 bundle 的字节长度,解压成功后写入 `.bundle-stamp`
2. **关键文件校验** —— 每次启动都查这 8 个文件是否都在:
   `runtime/python.exe`、`runtime/Lib/site-packages/segno/__init__.py`、
   `core/main.py`、`services/qq_service.py`、`services/setup_service.py`、
   `fatfish/NapCat/node.exe`、`fatfish/NapCat/index.js`、`fatfish/bili/qr_login.py`
3. **需要解压的条件**: 戳不一致 **或** 任一关键文件缺失
4. **解压前清理**: 清空 `runtime/`、`core/`、`services/`、`fatfish/` 下的所有内容,
   **但保留每一层里的 `data` 目录**(账号库、聊天记录、记忆、登录态都在里面)
5. **校验通过才写戳**,失败不写,下次启动自动重试

这条机制保证:以后每发一版,老用户覆盖 exe 后会自动换成全新的运行时,
不会出现"旧文件残留导致新功能缺失"。

**诊断**: 程序目录下的 `startup.log` 记录每次启动的解压过程
(版本号、bundle 大小、是否解压、清理了什么、tar 是否成功、缺哪些文件)。
用户报"QQ 用不了 / B站 出不了码"时,先要这个文件。

**已知前提**: 升级时程序不能正在运行,否则文件被占用删不掉。
删不掉的项会写进 `startup.log`。
