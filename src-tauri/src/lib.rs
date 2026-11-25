// Rivulet 若 · 桌面端内核
// 只做三件事:开窗口、保管登录态(DPAPI)、把 Core 的账号接口翻成本地 IPC。
mod api;
mod home;
mod session;

use std::sync::{Arc, Mutex};

use serde::Serialize;
use serde_json::{json, Value};
use tauri::State;

struct Inner {
    token: Option<String>,
    base: String,
}

pub struct AppState {
    inner: Mutex<Inner>,
    store: session::Store,
}

#[derive(Serialize, Clone)]
pub struct UserView {
    id: i64,
    username: String,
    role: String,
}

#[derive(Serialize)]
pub struct SessionView {
    user: UserView,
    expires_at: String,
}

fn user_from(v: &Value) -> Result<UserView, String> {
    let u = v.get("user").ok_or_else(|| "服务器回包里没有用户信息".to_string())?;
    Ok(UserView {
        id: u.get("id").and_then(|x| x.as_i64()).unwrap_or(0),
        username: u.get("username").and_then(|x| x.as_str()).unwrap_or_default().to_string(),
        role: u.get("role").and_then(|x| x.as_str()).unwrap_or("user").to_string(),
    })
}

fn lock(state: &AppState) -> Result<std::sync::MutexGuard<'_, Inner>, String> {
    state.inner.lock().map_err(|_| "内部状态异常,重启一下".to_string())
}

// ---------------- 自述 ----------------

#[tauri::command]
fn app_info(state: State<'_, AppState>) -> Value {
    let (base, logged_in) = match lock(&state) {
        Ok(inner) => (inner.base.clone(), inner.token.is_some()),
        Err(_) => (String::new(), false),
    };
    json!({
        "name": "Rivulet 若",
        "version": env!("CARGO_PKG_VERSION"),
        "api_base": base,
        "logged_in": logged_in,
        "os": std::env::consts::OS,
    })
}

#[tauri::command]
fn get_api_base(state: State<'_, AppState>) -> String {
    match lock(&state) {
        Ok(inner) => inner.base.clone(),
        Err(_) => String::new(),
    }
}

#[tauri::command]
fn set_api_base(base: String, state: State<'_, AppState>) -> Result<String, String> {
    state.store.set_api_base(&base)?;
    let saved = state.store.api_base();
    if let Ok(mut inner) = lock(&state) {
        inner.base = saved.clone();
    }
    Ok(saved)
}

// ---------------- 账号 ----------------

#[tauri::command]
async fn login(
    username: String,
    password: String,
    remember: bool,
    state: State<'_, AppState>,
) -> Result<SessionView, String> {
    let base = lock(&state)?.base.clone();
    let client = api::Client::new(&base, None);
    let res = client
        .post("/api/auth/login", &json!({ "username": username.trim(), "password": password }))
        .map_err(|e| e.message)?;

    let token = res
        .get("token")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "服务器没给登录凭证".to_string())?
        .to_string();
    let expires_at = res.get("expires_at").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    let user = user_from(&res)?;

    if remember {
        state.store.save_token(&token)?;
    }
    lock(&state)?.token = Some(token);

    Ok(SessionView { user, expires_at })
}

#[tauri::command]
async fn restore(state: State<'_, AppState>) -> Result<Option<SessionView>, String> {
    let token = match state.store.load_token() {
        Some(t) => t,
        None => return Ok(None),
    };
    let base = lock(&state)?.base.clone();
    let client = api::Client::new(&base, Some(&token));

    match client.get("/api/auth/me") {
        Ok(res) => {
            let user = user_from(&res)?;
            lock(&state)?.token = Some(token);
            Ok(Some(SessionView { user, expires_at: String::new() }))
        }
        Err(e) if e.status == Some(401) || e.status == Some(403) => {
            state.store.clear_token();
            Ok(None)
        }
        Err(e) => Err(e.message),
    }
}

#[tauri::command]
async fn logout(state: State<'_, AppState>) -> Result<(), String> {
    let token = lock(&state)?.token.clone();
    let base = lock(&state)?.base.clone();
    if let Some(t) = token {
        let client = api::Client::new(&base, Some(&t));
        // 服务器那边失败也要把本地清掉,不然卡在"假登录"
        let _ = client.post("/api/auth/logout", &json!({}));
    }
    lock(&state)?.token = None;
    state.store.clear_token();
    Ok(())
}

#[tauri::command]
async fn me(state: State<'_, AppState>) -> Result<Value, String> {
    let token = lock(&state)?.token.clone().ok_or_else(|| "还没登录".to_string())?;
    let base = lock(&state)?.base.clone();
    api::Client::new(&base, Some(&token)).get("/api/auth/me").map_err(|e| e.message)
}

#[tauri::command]
async fn change_password(
    old_password: String,
    new_password: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let token = lock(&state)?.token.clone().ok_or_else(|| "还没登录".to_string())?;
    let base = lock(&state)?.base.clone();
    api::Client::new(&base, Some(&token))
        .post("/api/auth/password", &json!({ "old_password": old_password, "new_password": new_password }))
        .map_err(|e| e.message)?;
    // 改完所有会话都失效,本地也要跟着退出
    lock(&state)?.token = None;
    state.store.clear_token();
    Ok(())
}

#[tauri::command]
async fn register(username: String, password: String, state: State<'_, AppState>) -> Result<Value, String> {
    let base = lock(&state)?.base.clone();
    api::Client::new(&base, None)
        .post(
            "/api/auth/register",
            &json!({ "username": username.trim(), "password": password }),
        )
        .map_err(|e| e.message)
}

// ---------------- 看数据(只读) ----------------

#[tauri::command]
async fn nodes_list(state: State<'_, AppState>) -> Result<Value, String> {
    let token = lock(&state)?.token.clone().ok_or_else(|| "还没登录".to_string())?;
    let base = lock(&state)?.base.clone();
    api::Client::new(&base, Some(&token)).get("/api/nodes").map_err(|e| e.message)
}

#[tauri::command]
async fn cmds_list(limit: Option<u32>, state: State<'_, AppState>) -> Result<Value, String> {
    let token = lock(&state)?.token.clone().ok_or_else(|| "还没登录".to_string())?;
    let base = lock(&state)?.base.clone();
    let n = limit.unwrap_or(50).clamp(1, 200);
    api::Client::new(&base, Some(&token))
        .get(&format!("/api/cmd?limit={}", n))
        .map_err(|e| e.message)
}

#[tauri::command]
async fn cmds_stats(state: State<'_, AppState>) -> Result<Value, String> {
    let token = lock(&state)?.token.clone().ok_or_else(|| "还没登录".to_string())?;
    let base = lock(&state)?.base.clone();
    api::Client::new(&base, Some(&token)).get("/api/cmd/stats").map_err(|e| e.message)
}

#[tauri::command]
async fn list_users(state: State<'_, AppState>) -> Result<Value, String> {
    let token = lock(&state)?.token.clone().ok_or_else(|| "还没登录".to_string())?;
    let base = lock(&state)?.base.clone();
    api::Client::new(&base, Some(&token)).get("/api/auth/users").map_err(|e| e.message)
}

/// 模块总览:走本地状态聚合服务(18090),不需要登录 Core。
#[tauri::command]
async fn overview() -> Result<Value, String> {
    let base = std::env::var("RIVULET_STATUS")
        .unwrap_or_else(|_| "http://127.0.0.1:18090".to_string());
    api::Client::new(&base, None)
        .get("/api/overview")
        .map_err(|e| format!("状态服务没起来?{}", e.message))
}

fn urlencode(s: &str) -> String {
    let mut out = String::new();
    for b in s.as_bytes() {
        match *b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(*b as char),
            b' ' => out.push_str("%20"),
            _ => out.push_str(&format!("%{:02X}", b)),
        }
    }
    out
}

/// 记忆搜索:走本地记忆服务(18091)。可按来源、条数取(记忆树用)。
#[tauri::command]
async fn memory_search(q: String, limit: Option<u32>, source: Option<String>) -> Result<Value, String> {
    let base = std::env::var("RIVULET_MEMORY")
        .unwrap_or_else(|_| "http://127.0.0.1:18091".to_string());
    let n = limit.unwrap_or(12).clamp(1, 500);
    let mut path = format!("/api/memory/search?limit={}&q={}", n, urlencode(q.trim()));
    if let Some(s) = source {
        if !s.is_empty() {
            path.push_str(&format!("&source={}", urlencode(&s)));
        }
    }
    api::Client::new(&base, None)
        .get(&path)
        .map_err(|e| format!("记忆服务没起来?{}", e.message))
}

/// 改记忆的 context(置顶/最后访问/强度都存在这里,不动表结构)。
#[tauri::command]
async fn memory_patch(id: String, context: Value) -> Result<Value, String> {
    let base = std::env::var("RIVULET_MEMORY")
        .unwrap_or_else(|_| "http://127.0.0.1:18091".to_string());
    api::Client::new(&base, None)
        .patch(&format!("/api/memory/{}", urlencode(&id)), &json!({ "context": context }))
        .map_err(|e| e.message)
}

// ---------------- 外部功能服务:QQ(18096)/ B站(18097) ----------------
// 面板只跟这两个本地服务说话;NapCat、B站 请求都在服务里跑,面板不露第三方窗口。

fn svc_base(service: &str) -> Result<String, String> {
    match service {
        "qq" => Ok(std::env::var("RIVULET_QQ")
            .unwrap_or_else(|_| "http://127.0.0.1:18096".to_string())),
        "bili" => Ok(std::env::var("RIVULET_BILI")
            .unwrap_or_else(|_| "http://127.0.0.1:18097".to_string())),
        "setup" => Ok(std::env::var("RIVULET_SETUP")
            .unwrap_or_else(|_| "http://127.0.0.1:18100".to_string())),
        "workspace" => Ok(std::env::var("RIVULET_WORKSPACE")
            .unwrap_or_else(|_| "http://127.0.0.1:18095".to_string())),
        "persona" => Ok(std::env::var("RIVULET_PERSONA")
            .unwrap_or_else(|_| "http://127.0.0.1:18098".to_string())),
        "mood" => Ok(std::env::var("RIVULET_MOOD")
            .unwrap_or_else(|_| "http://127.0.0.1:18099".to_string())),
        other => Err(format!("不认识的本地服务:{other}")),
    }
}

/// GET 一个本地功能服务的接口。service ∈ {"qq","bili"}
#[tauri::command]
async fn svc_get(service: String, path: String) -> Result<Value, String> {
    let base = svc_base(&service)?;
    api::Client::new(&base, None)
        .get(&path)
        .map_err(|e| format!("{service} 服务没起来?{}", e.message))
}

/// POST 一个本地功能服务的接口。
#[tauri::command]
async fn svc_post(service: String, path: String, body: Value) -> Result<Value, String> {
    let base = svc_base(&service)?;
    let mut client = api::Client::new(&base, None);
    if service == "workspace" && matches!(path.as_str(), "/api/chat" | "/api/generate") {
        client = client.with_timeout(std::time::Duration::from_secs(70));
    } else if matches!(path.as_str(), "/api/models/configure" | "/api/model/configure"
        | "/api/test_model" | "/api/models/test")
    {
        client = client.with_timeout(std::time::Duration::from_secs(28));
    }
    client
        .post(&path, &body)
        .map_err(|e| format!("{service} 服务没起来?{}", e.message))
}

/// PATCH 一个本地功能服务的接口(工作区改模型状态用)。
#[tauri::command]
async fn svc_patch(service: String, path: String, body: Value) -> Result<Value, String> {
    let base = svc_base(&service)?;
    api::Client::new(&base, None)
        .patch(&path, &body)
        .map_err(|e| format!("{service} 服务没起来?{}", e.message))
}

// ---------------- 鱼的小窝:本地引擎(见 home.rs,不走 HTTP) ----------------

// ---------------- 本地功能服务(QQ / B站)自启 ----------------
// exe 同级放 services\qq_service.py、bili_service.py;没起就在后台拉起来,
// 面板里只出现 Rivulet 若,第三方(NapCat / B站请求)全在服务里跑。

fn port_open(port: u16) -> bool {
    use std::net::{SocketAddr, TcpStream};
    use std::time::Duration as D;
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    TcpStream::connect_timeout(&addr, D::from_millis(300)).is_ok()
}

// ---------------- 单文件模式:内嵌 runtime/core/services/NapCat ----------------
// 发布版把运行时压成一个 bundle.zip 编进 exe,用户只下一个 exe。
#[cfg(rivulet_embedded_bundle)]
static BUNDLE: &[u8] = include_bytes!("../assets/bundle.zip");
#[cfg(not(rivulet_embedded_bundle))]
static BUNDLE: &[u8] = &[];

#[cfg(windows)]
fn hide_window(cmd: &mut std::process::Command) {
    use std::os::windows::process::CommandExt;
    cmd.creation_flags(0x0800_0000);
}

/// 必须存在的关键文件;缺任何一个都说明解压不完整。
const MUST_HAVE: &[&str] = &[
    "runtime/python.exe",
    "runtime/Lib/site-packages/segno/__init__.py",
    "runtime/Lib/site-packages/websockets/__init__.py",
    "core/main.py",
    "services/bili_service.py",
    "services/napcat.py",
    "services/qq_service.py",
    "services/setup_service.py",
    "services/workspace/api.py",
    "services/workspace/provider_llm.py",
    "fatfish/NapCat/node.exe",
    "fatfish/NapCat/index.js",
    "fatfish/bili/qr_login.py",
];

fn missing_files(exe_dir: &std::path::Path) -> Vec<String> {
    MUST_HAVE
        .iter()
        .filter(|m| !exe_dir.join(m).exists())
        .map(|m| m.to_string())
        .collect()
}

/// 解压前清空旧的程序文件,只保留 user data(data 目录)。
/// 不清的话,升级后旧文件和新包混在一起,缺的永远补不上
/// (QQ 找不到协议端、B站 出不了码),每发一版都会重犯。
fn wipe_program_dirs(exe_dir: &std::path::Path, log: &mut Vec<String>) {
    for name in ["runtime", "core", "services", "fatfish"] {
        let dir = exe_dir.join(name);
        if !dir.is_dir() {
            continue;
        }
        let rd = match std::fs::read_dir(&dir) {
            Ok(r) => r,
            Err(e) => {
                log.push(format!("读不到 {}: {}", dir.display(), e));
                continue;
            }
        };
        for ent in rd.flatten() {
            let file_name = ent.file_name();
            if file_name == "data"
                || (name == "services" && matches!(file_name.to_str(), Some("qq.json" | "bili.json")))
            {
                continue; // 包含旧版仍放在 services 根目录的用户配置。
            }
            let p = ent.path();
            let r = if p.is_dir() {
                std::fs::remove_dir_all(&p)
            } else {
                std::fs::remove_file(&p)
            };
            if let Err(e) = r {
                // 常见原因:用户还开着程序,文件被占用
                log.push(format!("删不掉 {} ({})", p.display(), e));
            }
        }
    }
}

fn ensure_bundle() {
    let exe_dir = match std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
    {
        Some(d) => d,
        None => return,
    };
    if BUNDLE.is_empty() {
        return;
    }

    let stamp = exe_dir.join(".bundle-stamp");
    let want = BUNDLE.len().to_string();
    let mut log: Vec<String> = Vec::new();
    log.push(format!(
        "exe v{} / bundle {} 字节 / 目录 {}",
        env!("CARGO_PKG_VERSION"),
        BUNDLE.len(),
        exe_dir.display()
    ));

    let missing = missing_files(&exe_dir);
    let stamp_ok = std::fs::read_to_string(&stamp)
        .map(|h| h.trim() == want)
        .unwrap_or(false);

    if stamp_ok && missing.is_empty() {
        log.push("bundle: 已是最新,跳过解压".to_string());
        let _ = std::fs::write(exe_dir.join("startup.log"), log.join("\r\n"));
        return;
    }

    log.push(format!(
        "bundle: 需要解压(戳一致={} / 缺文件={})",
        stamp_ok,
        if missing.is_empty() { "无".to_string() } else { missing.join(",") }
    ));

    // 1) 先清旧程序文件(保留 data)
    wipe_program_dirs(&exe_dir, &mut log);
    log.push("已清理旧的程序文件(保留 data)".to_string());

    // 2) 落盘并解压
    let tmp = std::env::temp_dir().join("rivulet_bundle.zip");
    if let Err(e) = std::fs::write(&tmp, BUNDLE) {
        log.push(format!("写临时 zip 失败: {}", e));
        let _ = std::fs::write(exe_dir.join("startup.log"), log.join("\r\n"));
        return;
    }

    let mut cmd = std::process::Command::new("tar");
    cmd.arg("-xf").arg(&tmp).arg("-C").arg(&exe_dir);
    #[cfg(windows)]
    hide_window(&mut cmd);
    let ok = matches!(cmd.status(), Ok(s) if s.success());
    log.push(format!("tar 解压: {}", if ok { "成功" } else { "失败" }));

    if !ok {
        let ps = format!(
            "Expand-Archive -LiteralPath '{}' -DestinationPath '{}' -Force",
            tmp.display(),
            exe_dir.display()
        );
        let mut p2 = std::process::Command::new("powershell");
        p2.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &ps]);
        #[cfg(windows)]
        hide_window(&mut p2);
        let ok2 = matches!(p2.status(), Ok(s) if s.success());
        log.push(format!("后备解压: {}", if ok2 { "成功" } else { "失败" }));
    }
    let _ = std::fs::remove_file(&tmp);

    // 3) 校验:关键文件齐了才算成功,才写戳
    let still = missing_files(&exe_dir);
    if still.is_empty() {
        let _ = std::fs::write(&stamp, &want);
        log.push("校验通过,已记录版本戳".to_string());
    } else {
        log.push(format!("校验失败,仍缺: {}", still.join(",")));
    }
    let _ = std::fs::write(exe_dir.join("startup.log"), log.join("\r\n"));
}


fn ensure_services() {
    // 便携化:exe 同级放 runtime\python.exe + services\ + core\ 即可换机器直接跑。
    let exe_dir = match std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
    {
        Some(d) => d,
        None => return,
    };
    let dir = exe_dir.join("services");
    if !dir.exists() {
        return;
    }

    // 优先用自带的 runtime\python.exe;没有才退回 PATH 上的 python。
    let rt_py = exe_dir.join("runtime").join("python.exe");
    let py_exe = if rt_py.exists() {
        rt_py
    } else {
        std::path::PathBuf::from("python")
    };
    // PYTHONPATH:环境变量优先,否则用自带 runtime 的 site-packages(不再硬编码本机路径)。
    let rt_site = exe_dir.join("runtime").join("Lib").join("site-packages");
    let py_path = std::env::var("PYTHONPATH").unwrap_or_else(|_| rt_site.to_string_lossy().to_string());

    // 0) 本机账号服务(Core):端口 8088,exe 同级 core\main.py。
    //    以前这里没启动它,导致新机器上注册/登录直接失败。
    let core_dir = exe_dir.join("core");
    if core_dir.join("main.py").exists() && !port_open(8088) {
        let mut cmd = std::process::Command::new(&py_exe);
        cmd.arg("main.py")
            .current_dir(&core_dir)
            .env("PYTHONPATH", &py_path)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000);
        }
        let _ = cmd.spawn();
    }

    // 1) services.json 里登记的全部本地服务(Core / 记忆 / 状态 / 工作区 / 心情 / QQ / B站)
    //    格式:[{"port":18096,"script":"qq_service.py","cwd":"...","env":{"K":"V"}}]
    //    cwd 支持相对路径(相对 exe/services);绝对路径不存在时自动退回本目录。
    let cfg_path = dir.join("services.json");
    let mut started = 0usize;
    if let Ok(raw) = std::fs::read_to_string(&cfg_path) {
        if let Ok(list) = serde_json::from_str::<Vec<serde_json::Value>>(&raw) {
            for item in list {
                let port = item.get("port").and_then(|v| v.as_u64()).unwrap_or(0) as u16;
                let script = item.get("script").and_then(|v| v.as_str()).unwrap_or("");
                let cwd = item.get("cwd").and_then(|v| v.as_str()).unwrap_or("");
                if port == 0 || script.is_empty() || port_open(port) {
                    continue;
                }
                let work_dir = if cwd.is_empty() {
                    dir.clone()
                } else {
                    let p = std::path::PathBuf::from(cwd);
                    if p.is_absolute() && !p.exists() {
                        dir.clone()
                    } else if p.is_absolute() {
                        p
                    } else {
                        exe_dir.join(p)
                    }
                };
                let mut cmd = std::process::Command::new(&py_exe);
                cmd.arg(script)
                    .current_dir(&work_dir)
                    .env("PYTHONPATH", &py_path)
                    .stdin(std::process::Stdio::null())
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null());
                if let Some(envs) = item.get("env").and_then(|v| v.as_object()) {
                    for (k, v) in envs {
                        if let Some(sv) = v.as_str() {
                            cmd.env(k, sv);
                        }
                    }
                }
                #[cfg(windows)]
                {
                    use std::os::windows::process::CommandExt;
                    cmd.creation_flags(0x0800_0000);
                }
                if cmd.spawn().is_ok() {
                    started += 1;
                }
            }
        }
    }
    if started > 0 {
        std::thread::sleep(std::time::Duration::from_millis(600));
    }

    // 2) 兜底:services.json 没有时,至少把面板三个功能服务拉起来
    for (port, script) in [(18100u16, "setup_service.py"),
                           (18098, "persona_service.py"),
                           (18099, "mood_service.py"),
                           (18096, "qq_service.py"),
                           (18097, "bili_service.py")] {
        if port_open(port) {
            continue;
        }
        let mut cmd = std::process::Command::new(&py_exe);
        cmd.arg(script)
            .current_dir(&dir)
            .env("PYTHONPATH", &py_path)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000);
        }
        let _ = cmd.spawn();
    }
}

// ---------------- 起来 ----------------

// ---------------- 窗口控制 ----------------
// decorations=false(自绘),系统不再提供最小化/最大化/关闭,必须自己实现,
// 否则窗口根本关不掉 —— 前端顶栏那三个按钮就是调这三个命令。
#[tauri::command]
fn win_min(window: tauri::Window) {
    let _ = window.minimize();
}

#[tauri::command]
fn win_max(window: tauri::Window) {
    if window.is_maximized().unwrap_or(false) {
        let _ = window.unmaximize();
    } else {
        let _ = window.maximize();
    }
}

#[tauri::command]
fn win_close(window: tauri::Window) {
    let _ = window.close();
}

#[tauri::command]
fn open_model_site(provider: String) -> Result<(), String> {
    let url = match provider.as_str() {
        "deepseek" => "https://platform.deepseek.com/api_keys",
        "qwen" => "https://help.aliyun.com/zh/model-studio/get-api-key",
        "openai" => "https://platform.openai.com/api-keys",
        _ => return Err("这个服务商没有预置的密钥页面".to_string()),
    };
    #[cfg(windows)]
    return std::process::Command::new("explorer.exe")
        .arg(url)
        .spawn()
        .map(|_| ())
        .map_err(|_| "打开浏览器失败".to_string());
    #[cfg(not(windows))]
    Err(format!("请在浏览器打开 {url}"))
}

/// 打开系统文件选择框,返回绝对路径(取消返回空串)。kind: video / image / any
#[tauri::command]
async fn pick_file(kind: String) -> Result<String, String> {
    let filter = match kind.as_str() {
        "video" => "视频文件|*.mp4;*.flv;*.mkv;*.mov;*.avi;*.webm;*.ts|所有文件|*.*",
        "image" => "图片文件|*.png;*.jpg;*.jpeg;*.webp;*.bmp|所有文件|*.*",
        _ => "所有文件|*.*",
    };
    let script = format!(
        "Add-Type -AssemblyName System.Windows.Forms; \
         $d = New-Object System.Windows.Forms.OpenFileDialog; \
         $d.Filter = '{}'; $d.Multiselect = $false; $d.Title = 'Rivulet 若'; \
         if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{ [Console]::Out.Write($d.FileName) }}",
        filter
    );
    let mut cmd = std::process::Command::new("powershell");
    cmd.args(["-NoProfile", "-STA", "-Command", &script])
        .stdin(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000);
    }
    let out = cmd.output().map_err(|e| format!("打开文件选择框失败:{e}"))?;
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

pub fn run() {
    let store = session::Store::new();
    let base = store.api_base();
    let remembered = store.load_token();

    ensure_bundle();
    ensure_services();

    // 鱼的小窝:纯本地引擎,状态在 exe 同级 data/home.json。
    // 背景线程每 20 秒推进一次:角色作息、作物、工资、每 3 小时家具整理。
    let home_engine = Arc::new(Mutex::new(home::HomeEngine::open_default()));
    let tick_engine = home_engine.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(std::time::Duration::from_secs(20));
        if let Ok(mut e) = tick_engine.lock() {
            let _ = e.tick_at(home::now_ts());
        }
    });

    tauri::Builder::default()
        .manage(AppState { inner: Mutex::new(Inner { token: remembered, base }), store })
        .manage(home::HomeStore(home_engine))
        .invoke_handler(tauri::generate_handler![
            app_info,
            get_api_base,
            set_api_base,
            login,
            restore,
            logout,
            me,
            change_password,
            register,
            nodes_list,
            cmds_list,
            cmds_stats,
            list_users,
            overview,
            memory_search,
            memory_patch,
            svc_get,
            svc_post,
            svc_patch,
            pick_file,
            win_min,
            win_max,
            win_close,
            open_model_site,
            home::home_snapshot,
            home::home_action,
            home::home_status,
            home::home_adjust_now
        ])
        .run(tauri::generate_context!())
        .expect("Rivulet 若 启动失败");
}
