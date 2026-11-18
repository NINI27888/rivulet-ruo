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

## 五、已执行 / 待授权

已执行：`git init`、脱敏自检（退出码 0）、`git add`（逐项审查）、`git commit`、
4 份 `routes_auth.py` 同源缺陷同步修复。

**唯一剩余**：推送到 GitHub 需要账号认证。当前环境无 `gh` CLI、无 git 身份配置、
无 token / 凭证助手、无已绑定连接器。网络可达 api.github.com，但 GitHub 不允许匿名
建仓库或 push。已找到官方 **GitHub 连接器**（可在 GitHub 上创建仓库、推送代码、
管理 PR），需在界面完成一次 OAuth 授权；授权后由我建仓并 push。

## 六、授权后的推送步骤（由我执行）

```bash
cd .../rivulet-app/github-release
python check_privacy.py          # 退出码必须为 0
# 通过连接器创建远程仓库 → git remote add origin <地址>
git branch -M main
git push -u origin main
```

**Public 前最后确认**：再跑一次 `check_privacy.py`，并核对 `git status` 中
没有 `data/`、`*.db`、`session.bin` 等。

## 七、后续可选

- admin 初始账号：生产 Core 不含 `/api/auth/register`，如需可补一份部署运维文档
  说明如何用管理接口初始化首个 admin
