/* Rivulet 若 · 桌面端 v0.3
   运行方式:
   1) Rivulet 若.exe(Tauri 2):走 invoke,登录态由 Rust/DPAPI 保管;
   2) 浏览器预览:退化成 fetch。
   注意:所有数字都来自真实接口(账号服务 / 状态聚合 / 记忆),没接的写「未接入」。 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var tauri = (window.__TAURI__ && window.__TAURI__.core) || null;
  var httpToken = null;
  var state = { user: null, nodes: [], cmds: [], stats: null, overview: null, view: "home", mem: null, qq: null, bili: null, mood: null };
  var API_BASE = "http://127.0.0.1:8088";

  // ---------------- 主题 ----------------
  var savedTheme = localStorage.getItem("rivulet.theme.v4") || "dark";
  setTheme(savedTheme === "light" ? "light" : "dark");
  $("btnTheme").addEventListener("click", function () {
    setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
  });
  function setTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("rivulet.theme.v4", t);
    // 图标由 CSS 按 data-theme 切换,这里只补 title,别的不要动 #btnTheme 内容
    var b = $("btnTheme");
    if (b) {
      var tip = t === "dark" ? "切换到白天温室" : "切换到夜晚温室";
      b.setAttribute("title", tip);
      b.setAttribute("aria-label", tip);
    }
  }

  // ---------------- 提示 ----------------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtTime(iso) {
    if (!iso) return "—";
    var d = new Date(String(iso).replace(" ", "T") + (String(iso).indexOf("Z") > 0 ? "" : "Z"));
    if (isNaN(d.getTime())) return String(iso);
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function statusCn(s) {
    return { pending: "待执行", delivered: "已投递", done: "已完成", failed: "失败", cancelled: "已取消" }[s] || (s || "—");
  }
  function statusCls(s) {
    return { done: "ok", failed: "bad", cancelled: "muted", pending: "warn", delivered: "" }[s] || "";
  }

  // ---------------- 后端桥 ----------------
  function api(method, path, body) {
    var headers = { "Content-Type": "application/json", "Accept": "application/json" };
    if (httpToken) headers.Authorization = "Bearer " + httpToken;
    return fetch(API_BASE + path, {
      method: method, headers: headers, body: body ? JSON.stringify(body) : undefined
    }).then(function (res) {
      return res.text().then(function (txt) {
        var data = {};
        try { data = txt ? JSON.parse(txt) : {}; } catch (e) {}
        if (!res.ok) {
          var msg = (data && data.detail) || ("服务器返回 " + res.status);
          var err = new Error(msg); err.status = res.status; throw err;
        }
        return data;
      });
    }, function () {
      // 桌面版连不上,几乎都是本机 core 没起来(缺文件 / 首次解压没完成),
      // 不是网络问题 —— 别再让用户去查网络。
      var tip = tauri
        ? ("本机账号服务没启动。" +
           "请确认你下载的是「单文件版」exe(25 MB 左右);" +
           "如果是第一次打开,等十几秒(它在解压运行环境)再试;" +
           "还不行就看程序目录下的 core\\core.log。")
        : "连不上账号服务,检查网络后重试";
      throw new Error(tip);
    });
  }

  function call(cmd, args) {
    if (tauri) return tauri.invoke(cmd, args || {});
    var map = {
      login: function () { return api("POST", "/api/auth/login", { username: args.username, password: args.password })
        .then(function (r) { httpToken = r.token; return { user: r.user, expires_at: r.expires_at }; }); },
      register: function () { return api("POST", "/api/auth/register", { username: args.username, password: args.password }); },
      restore: function () { return Promise.resolve(null); },
      logout: function () { httpToken = null; return Promise.resolve(); },
      me: function () { return api("GET", "/api/auth/me"); },
      nodes_list: function () { return api("GET", "/api/nodes"); },
      cmds_list: function () { return api("GET", "/api/cmd?limit=" + (args.limit || 50)); },
      cmds_stats: function () { return api("GET", "/api/cmd/stats"); },
      get_api_base: function () { return Promise.resolve(API_BASE); },
      overview: function () {
        return fetch("http://127.0.0.1:18090/api/overview").then(function (r) { return r.json(); });
      },
      memory_search: function () {
        var url = "http://127.0.0.1:18091/api/memory/search?limit=" + (args.limit || 12) +
          "&q=" + encodeURIComponent(args.q || "");
        if (args.source) url += "&source=" + encodeURIComponent(args.source);
        return fetch(url).then(function (r) { return r.json(); });
      },
      memory_patch: function () {
        return fetch("http://127.0.0.1:18091/api/memory/" + encodeURIComponent(args.id), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ context: args.context || {} })
        }).then(function (r) { return r.json(); });
      },
      svc_get: function () {
        var base = args.service === "qq" ? "http://127.0.0.1:18096" : (args.service === "bili" ? "http://127.0.0.1:18097" : (args.service === "persona" ? "http://127.0.0.1:18098" : (args.service === "mood" ? "http://127.0.0.1:18099" : "http://127.0.0.1:18095")));
        return fetch(base + args.path).then(function (r) { return r.json(); });
      },
      svc_post: function () {
        var base = args.service === "qq" ? "http://127.0.0.1:18096" : (args.service === "bili" ? "http://127.0.0.1:18097" : (args.service === "persona" ? "http://127.0.0.1:18098" : (args.service === "mood" ? "http://127.0.0.1:18099" : "http://127.0.0.1:18095")));
        return fetch(base + args.path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(args.body || {})
        }).then(function (r) { return r.json(); });
      },
      svc_patch: function () {
        var base = args.service === "qq" ? "http://127.0.0.1:18096" : (args.service === "bili" ? "http://127.0.0.1:18097" : (args.service === "persona" ? "http://127.0.0.1:18098" : (args.service === "mood" ? "http://127.0.0.1:18099" : "http://127.0.0.1:18095")));
        return fetch(base + args.path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args.body || {}) })
          .then(function (r) { return r.json(); });
      },
      app_info: function () { return Promise.resolve({ name: "Rivulet 若", version: "0.3.0-preview" }); }
    };
    if (!map[cmd]) return Promise.reject(new Error("这个动作只能在桌面版里用"));
    return map[cmd]();
  }

  // ---------------- 视图切换 ----------------
  function setErr(id, msg) { var el = $(id); if (el) el.textContent = msg || ""; }
  function showLogin() {
    $("appView").hidden = true; $("registerView").hidden = true; $("loginView").hidden = false;
    $("btnTheme").hidden = false;
    document.documentElement.setAttribute("data-auth", "1");
    setTimeout(function () { $("lgUser").focus(); }, 30);
  }
  function showRegister() {
    $("loginView").hidden = true; $("appView").hidden = true; $("registerView").hidden = false;
    $("btnTheme").hidden = false;
    document.documentElement.setAttribute("data-auth", "1");
    setTimeout(function () { $("rgUser").focus(); }, 30);
  }
  // ---------------- 新用户首启引导 → 一键部署 ----------------
  // 假设你是新用户:登录后不该直接落到首页,而应先引导到「一键部署」。
  // 判定只依赖本机 localStorage 标记 + 部署服务的 finished,不写任何私人内容。
  var SETUP_GUIDE_KEY = "ruo.setup.guideDone";
  function guideDone() {
    try { return localStorage.getItem(SETUP_GUIDE_KEY) === "1"; } catch (e) { return false; }
  }
  function markGuideDone() {
    try { localStorage.setItem(SETUP_GUIDE_KEY, "1"); } catch (e) { }
  }
  function showSetupGuide() {
    // 顶部一条轻引导条,说明「先做完这三步再用」,可关闭(关闭=跳过,不再强制)
    var bar = $("setupGuideBar");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "setupGuideBar";
      bar.className = "setupGuideBar";
      bar.innerHTML = '<span class="setupGuideIcon">✦</span>' +
        '<span class="setupGuideText"><b>第一次使用,先做三步</b> —— 扫码 / 填模型 Key,完成后就能正常用若了。</span>' +
        '<button type="button" id="setupGuideSkip" class="setupGuideSkip">跳过引导</button>';
      var host = document.querySelector(".appMain") || document.getElementById("appView");
      if (host) host.insertBefore(bar, host.firstChild);
      var sk = document.getElementById("setupGuideSkip");
      if (sk) sk.addEventListener("click", function () { markGuideDone(); bar.remove(); });
    }
  }
  function hideSetupGuide() {
    var bar = $("setupGuideBar"); if (bar) bar.remove();
  }
  // 部署状态变化时调用:三步全完成 → 自动写标记,之后不再强制引导
  function onSetupProgress(r) {
    if (r && r.finished) { markGuideDone(); hideSetupGuide(); }
  }

  function showApp() {
    $("loginView").hidden = true; $("registerView").hidden = true; $("appView").hidden = false;
    $("btnTheme").hidden = false;
    document.documentElement.removeAttribute("data-auth");
    renderSelf();
    // 新用户(未完成引导)先落到一键部署区,而不是首页
    var startView = guideDone() ? "home" : "setup";
    switchView(startView);
    if (startView === "setup") showSetupGuide();
    loadAll();
  }
  $("toRegister").addEventListener("click", showRegister);
  $("toLogin").addEventListener("click", showLogin);

  // ---------------- 登录 / 注册 ----------------
  var busyLogin = false;
  function doLogin() {
    if (busyLogin) return;
    setErr("lgErr", "");
    var u = $("lgUser").value.trim(), p = $("lgPass").value;
    if (!u) { setErr("lgErr", "用户名不能空着"); $("lgUser").focus(); return; }
    if (!p) { setErr("lgErr", "密码不能空着"); $("lgPass").focus(); return; }
    busyLogin = true; $("lgBtn").disabled = true; $("lgBtn").textContent = "进入中…";
    call("login", { username: u, password: p, remember: true })
      .then(function (s) { state.user = s.user; showApp(); })
      .catch(function (e) { setErr("lgErr", friendly(e)); })
      .then(function () { busyLogin = false; $("lgBtn").disabled = false; $("lgBtn").textContent = "进入温室"; });
  }
  $("lgBtn").addEventListener("click", doLogin);
  ["lgUser", "lgPass"].forEach(function (id) {
    $(id).addEventListener("keydown", function (e) { if (e.key === "Enter") doLogin(); });
  });

  var busyReg = false;
  function doRegister() {
    if (busyReg) return;
    setErr("rgErr", "");
    var u = $("rgUser").value.trim(), p = $("rgPass").value, p2 = $("rgPass2").value;
    if (!u) { setErr("rgErr", "用户名不能空着"); return; }
    if (p.length < 6) { setErr("rgErr", "密码至少要 6 位"); return; }
    if (p !== p2) { setErr("rgErr", "两次输入的密码不一样"); return; }
    busyReg = true; $("rgBtn").disabled = true; $("rgBtn").textContent = "创建中…";
    call("register", { username: u, password: p })
      .then(function () {
        $("lgUser").value = u; $("lgPass").value = "";
        $("lgErr").textContent = "账号建好了,直接登录就行。"; $("lgErr").style.color = "var(--ok)";
        showLogin(); $("lgPass").focus();
      })
      .catch(function (e) { setErr("rgErr", friendly(e)); })
      .then(function () { busyReg = false; $("rgBtn").disabled = false; $("rgBtn").textContent = "创建账号"; });
  }
  $("rgBtn").addEventListener("click", doRegister);
  ["rgUser", "rgPass", "rgPass2"].forEach(function (id) {
    $(id).addEventListener("keydown", function (e) { if (e.key === "Enter") doRegister(); });
  });

  function friendly(e) {
    var msg = (e && (e.message || e)) + "";
    if (/401|用户名或密码/.test(msg)) return "用户名或密码不对,再试一次";
    if (/403|停用/.test(msg)) return "这个账号已停用";
    if (/409|已存在/.test(msg)) return "这个用户名已经有人用了";
    if (/本机账号服务没启动/.test(msg)) return msg;
    if (/连不上|Failed to fetch|network/.test(msg)) {
      return tauri
        ? "本机账号服务没启动:确认下载的是「单文件版」exe(25 MB 左右),首次打开要等十几秒解压。"
        : "连不上账号服务,检查网络后重试";
    }
    return msg;
  }

  // ---------------- 退出 ----------------
  function doLogout() {
    state.user = null; state.nodes = []; state.cmds = []; state.stats = null; state.overview = null;
    $("lgPass").value = ""; setErr("lgErr", ""); showLogin();
    call("logout", {}).catch(function () {});
  }
  $("btnLogout").addEventListener("click", doLogout);
  $("btnLogout2").addEventListener("click", doLogout);

  // ---------------- 导航 ----------------
  var TITLES = {
    home: ["概览", "Rivulet 若 的整体状态"],
    fish: ["鱼的小窝", "两层小家 · 田地 · 角色与经营"],
    nodes: ["节点", "已经登记到 Core 的设备"],
    cmds: ["命令", "最近下发过的命令"],
    account: ["账号", "当前登录身份与安全"]
  };
  $("nav").addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a[data-p]") : null;
    if (!a) return;
    e.preventDefault();
    switchView(a.getAttribute("data-p"));
  });
  var NAV_TITLES = {
    home: "首页", fish: "鱼的小窝", memory: "记忆树", mood: "心情", persona: "人格",
    workspace: "工作区", bili: "B站 Agent", qq: "QQ Agent", setup: "一键部署",
    nodes: "节点", cmds: "命令", account: "账号", settings: "设置"
  };
  var NAV_PRIMARY = { home: 1, workspace: 1, bili: 1, qq: 1, fish: 1, settings: 1 };
  var NAV_SECONDARY = { memory: 1, mood: 1, persona: 1, setup: 1, nodes: 1, cmds: 1, account: 1 };
  var ALL_VIEWS = ["home", "fish", "memory", "mood", "persona", "qq", "bili", "workspace", "setup", "nodes", "cmds", "account", "settings"];
  function switchView(v) {
    if (ALL_VIEWS.indexOf(v) < 0) v = "home";
    state.view = v;
    // 主导航 6 项;次级页面(记忆/心情/人格/账号/节点/命令/部署)高亮「设置」
    var navKey = NAV_PRIMARY[v] ? v : (NAV_SECONDARY[v] ? "settings" : v);
    var links = $("nav") ? $("nav").querySelectorAll("a[data-p]") : [];
    for (var i = 0; i < links.length; i++) links[i].classList.toggle("on", links[i].getAttribute("data-p") === navKey);
    ALL_VIEWS.forEach(function (k) {
      var el = $("p-" + k); if (el) el.hidden = k !== v;
    });
    document.documentElement.setAttribute("data-page", v);
    // 切页后:初始化分页 + 把这一页的原生 select 换成玻璃下拉
    if (window.__initPaneTabs) setTimeout(function () { window.__initPaneTabs(); }, 0);
    if (window.__glassSelectAll) setTimeout(function () { window.__glassSelectAll(); }, 0);
    var view = $("view");
    if (view) view.scrollTop = 0;
    document.title = "Rivulet 若 · " + (NAV_TITLES[v] || v);
    if (v === "fish" && window.FishHome) window.FishHome.mount();
    if (v === "memory") enterMemory();
    if (v === "mood") enterMood();
    if (v === "persona") enterPersona();
    if (v === "workspace") enterWorkspace();
    if (v === "setup") enterSetup();
    if (v === "qq") enterQq();
    if (v === "bili") enterBili();
    try { window.dispatchEvent(new CustomEvent("ruo:view", { detail: v })); } catch (e) { }
  }
  // 给 home.js / 其它脚本用的最小 API
  try {
    window.RuoApp = {
      $: $, state: state,
      call: call,
      svcGet: typeof svcGet === "function" ? svcGet : function (s, p) { return call("svc_get", { service: s, path: p }); },
      svcPost: typeof svcPost === "function" ? svcPost : function (s, p, b) { return call("svc_post", { service: s, path: p, body: b || {} }); },
      svcPatch: function (s, p, b) { return call("svc_patch", { service: s, path: p, body: b || {} }); },
      switchView: switchView,
      toast: typeof toast === "function" ? toast : function () { }
    };
    window.RuoAppReady = true;
    window.dispatchEvent(new CustomEvent("ruo:ready"));
  } catch (e) { }

  // ---------------- 读数据 ----------------
  function loadAll() {
    Promise.all([
      call("me", {}).catch(function () { return null; }),
      call("nodes_list", {}).catch(function (e) { return { __err: e.message || e }; }),
      call("cmds_list", { limit: 50 }).catch(function (e) { return { __err: e.message || e }; }),
      call("cmds_stats", {}).catch(function (e) { return { __err: e.message || e }; }),
      call("overview", {}).catch(function (e) { return { __err: e.message || e }; }),
      call("home_status", {}).catch(function () { return null; }),
      call("memory_search", { q: "", limit: 300 }).catch(function (e) { return { __err: e.message || e }; }),
      call("svc_get", { service: "qq", path: "/api/status" }).catch(function (e) { return { __err: e.message || e }; }),
      call("svc_get", { service: "bili", path: "/api/status" }).catch(function (e) { return { __err: e.message || e }; }),
      call("svc_get", { service: "mood", path: "/api/state" }).catch(function (e) { return { __err: e.message || e }; }),
      call("svc_get", { service: "persona", path: "/api/persona" }).catch(function (e) { return { __err: e.message || e }; }),
      call("svc_get", { service: "workspace", path: "/api/models/status" }).catch(function (e) { return { __err: e.message || e }; }),
      call("svc_get", { service: "setup", path: "/api/setup/status" }).catch(function (e) { return { __err: e.message || e }; })
    ]).then(function (r) {
      if (r[0] && r[0].user) state.user = r[0].user;
      state.nodes = (r[1] && r[1].nodes) || [];
      state.cmds = (r[2] && r[2].commands) || [];
      state.stats = r[3] || null;
      state.overview = (r[4] && !r[4].__err) ? r[4] : null;
      state.overviewErr = (r[4] && r[4].__err) || "";
      state.homeLocal = (r[5] && r[5].ok !== false) ? r[5] : null;
      if (r[6] && !r[6].__err) applyMemory(r[6].items || [], false);
      else if (r[6] && r[6].__err) memFailed(r[6].__err);
      state.qq = (r[7] && !r[7].__err) ? r[7] : null;
      state.qqErr = (r[7] && r[7].__err) || "";
      state.bili = (r[8] && !r[8].__err) ? r[8] : null;
      state.biliErr = (r[8] && r[8].__err) || "";
      state.mood = (r[9] && !r[9].__err) ? r[9] : null;
      state.moodErr = (r[9] && r[9].__err) || "";
      state.persona = (r[10] && r[10].data) ? r[10].data : null;
      state.ws = (r[11] && !r[11].__err) ? r[11] : null;
      if (r[12] && !r[12].__err) {
        SETUP.steps = r[12].steps || []; SETUP.done = r[12].done || 0; SETUP.total = r[12].total || 3;
        renderSetupRail();
      }
      renderSelf(); renderHead(); renderToday(); renderStats(); renderQuick(); renderCmds();
      renderDetail(); renderNodes(); renderCmdsPage(); renderAccount(); renderWeather();
      renderSvcRail(); renderMoodRail();
      try { window.dispatchEvent(new CustomEvent("ruo:data")); } catch (e) { }
      if (state.view === "qq") renderQqStatus();
      if (state.view === "mood") renderMoodPage();
      if (state.view === "bili") renderBiliStatus();
    });
  }
  $("btnRefresh").addEventListener("click", loadAll);
  setInterval(function () { if (state.user && !$("appView").hidden) loadAll(); }, 30000);

  // ---------------- 渲染 ----------------
  function moduleOf(name) {
    var mods = (state.overview && state.overview.modules) || [];
    for (var i = 0; i < mods.length; i++) if (mods[i].name === name) return mods[i];
    return null;
  }

  function renderSelf() {
    var u = state.user || {};
    var nick = localStorage.getItem("rivulet.nick") || u.username || "—";
    $("meName").textContent = nick;
    $("meRole").textContent = (u.username || "—") + (u.role ? " · " + u.role : "");
    renderAvatar();
  }

  // ---------------- 头像:可以从相册选一张 ----------------
  var AVA = {
    whale: '<svg viewBox="0 0 16 16"><path d="M3 6h7v1h2v1h1v2h-1v1H4v-1H2V8h1z" fill="#7FA9C4"/><rect x="12" y="5" width="2" height="2" fill="#5C87A3"/><rect x="4" y="9" width="7" height="1" fill="#B7D6E4"/><circle cx="5" cy="8" r="1" fill="#1F2E38"/><rect x="2" y="5" width="1" height="1" fill="#B7D6E4"/></svg>',
    cat: '<svg viewBox="0 0 16 16"><path d="M4 5h1V3h2v1h2V3h2v2h1v6H4z" fill="#D8B48C"/><rect x="5" y="8" width="2" height="2" fill="#3A3128"/><rect x="9" y="8" width="2" height="2" fill="#3A3128"/><rect x="7" y="10" width="2" height="1" fill="#B98D68"/></svg>',
    plant: '<svg viewBox="0 0 16 16"><rect x="6" y="7" width="4" height="7" fill="#C08A63"/><rect x="4" y="3" width="2" height="5" fill="#6E9160"/><rect x="10" y="2" width="2" height="6" fill="#7FA06A"/><rect x="7" y="1" width="2" height="6" fill="#8FAE86"/></svg>',
    fish: '<svg viewBox="0 0 16 16"><path d="M2 8h2V6h5v1h2l3-2v6l-3-2h-2v1H4V9H2z" fill="#E8A33C"/><circle cx="5" cy="8" r="1" fill="#3A3128"/><rect x="3" y="4" width="1" height="1" fill="#A9D3D8"/></svg>',
    moon: '<svg viewBox="0 0 16 16"><path d="M9 2h2v1h2v2h1v6h-1v2h-2v1H7v-1H5v-2H3V5h2V3h4z" fill="#B7D8BC"/><rect x="6" y="6" width="3" height="3" fill="#8FAE86"/></svg>',
    star: '<svg viewBox="0 0 16 16"><path d="M7 2h2v3h3v2h3v2h-3v2H9v3H7v-3H4v-2H1V7h3V5h3z" fill="#F2C24E"/></svg>'
  };
  function renderAvatar() {
    var img = localStorage.getItem("rivulet.avatarImg");
    if (img) {
      $("meAva").innerHTML = '<img alt="" src="' + img + '">';
      return;
    }
    var id = localStorage.getItem("rivulet.avatar") || "whale";
    $("meAva").innerHTML = AVA[id] || AVA.whale;
  }

  // 把相册图片压成 96x96 的 dataURL(不依赖后端、不联网)
  function usePhoto(file) {
    if (!file) return;
    var fr = new FileReader();
    fr.onload = function () {
      var im = new Image();
      im.onload = function () {
        var S2 = 96, cv2 = document.createElement("canvas");
        cv2.width = S2; cv2.height = S2;
        var g = cv2.getContext("2d");
        var side = Math.min(im.width, im.height);
        g.drawImage(im, (im.width - side) / 2, (im.height - side) / 2, side, side, 0, 0, S2, S2);
        try {
          localStorage.setItem("rivulet.avatarImg", cv2.toDataURL("image/png"));
          localStorage.removeItem("rivulet.avatar");
        } catch (e) {
          alert("这张图存不下,换张小一点的");
          return;
        }
        renderAvatar();
      };
      im.src = String(fr.result);
    };
    fr.readAsDataURL(file);
  }

  $("meAva").addEventListener("click", function (e) {
    e.stopPropagation();
    var old = document.getElementById("avatarPick");
    if (old) { old.remove(); return; }
    var pick = document.createElement("div");
    pick.id = "avatarPick"; pick.className = "avatarPick";
    pick.innerHTML =
      '<button type="button" class="avWide" id="avPhoto">🖼 从相册选一张</button>' +
      '<button type="button" class="avWide" id="avReset">恢复默认像素头像</button>';
    Object.keys(AVA).forEach(function (k) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "avBtn"; b.innerHTML = AVA[k]; b.title = k;
      b.addEventListener("click", function (ev) {
        ev.stopPropagation();
        localStorage.removeItem("rivulet.avatarImg");
        localStorage.setItem("rivulet.avatar", k);
        renderAvatar(); pick.remove();
      });
      pick.appendChild(b);
    });
    document.body.appendChild(pick);
    var ar = $("meAva").getBoundingClientRect();
    pick.style.left = Math.max(8, ar.left - 6) + "px";
    pick.style.top = Math.max(48, ar.top - 210) + "px";
    pick.querySelector("#avPhoto").addEventListener("click", function (ev) {
      ev.stopPropagation();
      pick.remove();
      var inp = document.getElementById("avatarFile");
      if (!inp) {
        inp = document.createElement("input");
        inp.type = "file"; inp.id = "avatarFile"; inp.accept = "image/*";
        inp.style.display = "none";
        inp.addEventListener("change", function () { usePhoto(inp.files && inp.files[0]); inp.value = ""; });
        document.body.appendChild(inp);
      }
      inp.click();
    });
    pick.querySelector("#avReset").addEventListener("click", function (ev) {
      ev.stopPropagation();
      localStorage.removeItem("rivulet.avatarImg");
      localStorage.removeItem("rivulet.avatar");
      renderAvatar(); pick.remove();
    });
    setTimeout(function () { document.addEventListener("click", closePick, { once: true }); }, 0);
    function closePick() { if (pick) pick.remove(); }
  });

  // ---------------- 最左:时钟 + 小窝天气 ----------------
  var WX = {
    sun: '<svg viewBox="0 0 16 16"><rect x="6" y="1" width="4" height="2" fill="#E0A93B"/><rect x="6" y="13" width="4" height="2" fill="#E0A93B"/><rect x="1" y="6" width="2" height="4" fill="#E0A93B"/><rect x="13" y="6" width="2" height="4" fill="#E0A93B"/><rect x="3" y="3" width="2" height="2" fill="#E0A93B"/><rect x="11" y="3" width="2" height="2" fill="#E0A93B"/><rect x="3" y="11" width="2" height="2" fill="#E0A93B"/><rect x="11" y="11" width="2" height="2" fill="#E0A93B"/><rect x="5" y="5" width="6" height="6" fill="#F5C64F"/></svg>',
    cloud: '<svg viewBox="0 0 16 16"><rect x="3" y="7" width="10" height="4" fill="#B7C8CF"/><rect x="5" y="5" width="6" height="2" fill="#B7C8CF"/><rect x="2" y="9" width="12" height="2" fill="#8FA6AD"/><rect x="2" y="6" width="1" height="3" fill="#8FA6AD"/><rect x="13" y="6" width="1" height="3" fill="#8FA6AD"/></svg>',
    rain: '<svg viewBox="0 0 16 16"><rect x="3" y="5" width="10" height="4" fill="#9FB9C6"/><rect x="5" y="3" width="6" height="2" fill="#9FB9C6"/><rect x="4" y="11" width="1" height="3" fill="#4E8FA6"/><rect x="8" y="11" width="1" height="3" fill="#4E8FA6"/><rect x="12" y="11" width="1" height="3" fill="#4E8FA6"/></svg>',
    wind: '<svg viewBox="0 0 16 16"><rect x="2" y="5" width="9" height="2" fill="#7FA38C"/><rect x="4" y="8" width="10" height="2" fill="#7FA38C"/><rect x="2" y="11" width="7" height="2" fill="#7FA38C"/><rect x="13" y="4" width="1" height="2" fill="#5C8268"/></svg>',
    overcast: '<svg viewBox="0 0 16 16"><rect x="2" y="6" width="9" height="4" fill="#B0BFC4"/><rect x="4" y="4" width="5" height="2" fill="#B0BFC4"/><rect x="7" y="9" width="8" height="4" fill="#8E9EA4"/><rect x="9" y="7" width="5" height="2" fill="#8E9EA4"/></svg>'
  };

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function tickClock() {
    var d = new Date();
    $("clockBig").textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
    $("clockSec").textContent = ":" + pad(d.getSeconds());
    $("clockDate").textContent = (d.getMonth() + 1) + " 月 " + d.getDate() + " 日 · 周" + "日一二三四五六"[d.getDay()];
    var big2 = $("clockBig2"), date2 = $("clockDate2");
    if (big2) big2.textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
    if (date2) date2.textContent = d.getFullYear() + " · " + pad(d.getMonth() + 1) + " · " + pad(d.getDate());
  }
  setInterval(tickClock, 1000); tickClock();

  var WX_CN = { sunny: "晴", cloudy: "多云", rain: "小雨", wind: "微风", overcast: "阴" };
  function renderWeather() {
    // 优先用 exe 内置的小窝引擎(不再依赖 18094)
    var local = state.homeLocal || {};
    var h = (state.overview && state.overview.home) || {};
    var w = local.weather || h.weather || {};
    $("wxKind").textContent = w.name || WX_CN[w.kind] || w.kind || "—";
    $("wxTemp").textContent = (w.temp == null) ? "—" : (w.temp + "°");
    $("wxIcon").innerHTML = WX[w.icon] || WX.overcast;
  }

  // 改昵称:本地显示名,不动账号;刷新后还在
  $("btnNick").addEventListener("click", function () {
    var u = state.user || {};
    var cur = localStorage.getItem("rivulet.nick") || u.username || "";
    var box = document.createElement("input");
    box.value = cur; box.maxLength = 16; box.className = "nickEdit";
    var host = $("meName");
    host.style.display = "none";
    host.parentNode.insertBefore(box, host.nextSibling);
    box.focus(); box.select();
    function done(save) {
      if (save && box.value.trim()) localStorage.setItem("rivulet.nick", box.value.trim());
      box.remove(); host.style.display = "";
      renderSelf(); renderHead();
    }
    box.addEventListener("keydown", function (e) {
      if (e.key === "Enter") done(true);
      if (e.key === "Escape") done(false);
    });
    box.addEventListener("blur", function () { done(true); });
  });

  function renderHead() {
    var d = new Date();
    var wd = "日一二三四五六"[d.getDay()];
    $("hDate").textContent = d.getFullYear() + " 年 " + (d.getMonth() + 1) + " 月 " + d.getDate() + " 日 · 星期" + wd;
    var h = d.getHours(), g = "晚上好";
    if (h < 6) g = "凌晨好"; else if (h < 11) g = "早上好"; else if (h < 14) g = "中午好"; else if (h < 18) g = "下午好";
    var u = state.user || {};
    var nick = localStorage.getItem("rivulet.nick") || u.username || "你";
    if ($("greet")) $("greet").textContent = g + "," + nick;
    $("whoName").textContent = nick;
    $("whoRole").textContent = u.role || "—";
  }

  function renderToday() {
    var nodes = state.nodes || [];
    var online = nodes.filter(function (n) { return n.online; }).length;
    var byStatus = (state.stats && state.stats.commands_by_status) || {};
    var total = 0, pending = 0;
    Object.keys(byStatus).forEach(function (k) { total += byStatus[k]; if (k === "pending") pending = byStatus[k]; });

    var pend = (state.cmds || []).filter(function (c) { return c.status === "pending"; })[0];
    $("tTask").textContent = pend ? ("#" + pend.id + " " + pend.kind) : "空闲";
    var core = moduleOf("core");
    $("tCore").textContent = core ? (core.ok ? "在线 " + (core.latency_ms || 0) + " ms" : "离线") : "—";
    $("tNodes").textContent = nodes.length ? (online + " / " + nodes.length + " 台在线") : "还没有节点";
    $("tRun").textContent = "命令 " + total + " 条 · 待执行 " + pending;
  }

  function renderStats() {
    var nodes = state.nodes || [];
    var online = nodes.filter(function (n) { return n.online; }).length;
    var byStatus = (state.stats && state.stats.commands_by_status) || {};
    var total = 0, pending = 0;
    Object.keys(byStatus).forEach(function (k) { total += byStatus[k]; if (k === "pending") pending = byStatus[k]; });
    var cards = [
      { k: "设备数量", v: nodes.length, sub: "台", cls: "" },
      { k: "在线设备", v: online, sub: "台", cls: online ? "ok" : (nodes.length ? "bad" : "") },
      { k: "命令数量", v: total, sub: "条", cls: "" },
      { k: "待执行任务", v: pending, sub: "条", cls: pending ? "warn" : "" }
    ];
    $("homeStats").innerHTML = cards.map(function (c) {
      return '<div class="stat"><div class="k">' + c.k + '</div><div class="v ' + c.cls + '">' +
        c.v + "<small>" + c.sub + "</small></div></div>";
    }).join("");
  }

  var ICONS = {
    qq: '<svg viewBox="0 0 16 16" fill="none"><path d="M8 2c2.2 0 3.6 1.7 3.6 4v1l1.6 2.2c.3.4 0 .9-.5.9h-1.2c-.2 1.6-1.6 2.9-3.5 2.9s-3.3-1.3-3.5-2.9H3.3c-.5 0-.8-.5-.5-.9L4.4 7V6C4.4 3.7 5.8 2 8 2Z" stroke="currentColor" stroke-width="1.2"/><circle cx="6.4" cy="6.3" r=".8" fill="currentColor"/><circle cx="9.6" cy="6.3" r=".8" fill="currentColor"/></svg>',
    bili: '<svg viewBox="0 0 16 16" fill="none"><rect x="2.2" y="4.4" width="11.6" height="8.4" rx="2.2" stroke="currentColor" stroke-width="1.2"/><path d="M5.2 2.6 7 4.3M10.8 2.6 9 4.3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M6.6 7.2v2.4l2.4-1.2-2.4-1.2Z" fill="currentColor"/></svg>',
    memory: '<svg viewBox="0 0 16 16" fill="none"><ellipse cx="8" cy="4.4" rx="5.2" ry="2" stroke="currentColor" stroke-width="1.2"/><path d="M2.8 4.4v3.6c0 1.1 2.3 2 5.2 2s5.2-.9 5.2-2V4.4M2.8 8v3.6c0 1.1 2.3 2 5.2 2s5.2-.9 5.2-2V8" stroke="currentColor" stroke-width="1.2"/></svg>',
    home: '<svg viewBox="0 0 16 16" fill="none"><path d="M2.6 7.6 8 3l5.4 4.6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M4.2 7.2V13h7.6V7.2" stroke="currentColor" stroke-width="1.2"/><path d="M6.6 13v-3.2h2.8V13" stroke="currentColor" stroke-width="1.2"/></svg>',
    ws: '<svg viewBox="0 0 16 16" fill="none"><rect x="2.4" y="3" width="11.2" height="8.2" rx="1.6" stroke="currentColor" stroke-width="1.2"/><path d="M5.6 13.2h4.8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M5.4 6.2 7 7.7l-1.6 1.5M8.4 9.2h2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
  };

  var QUICK = [
    { k: "qq", name: "QQ 机器人", desc: "让 DSH 接入 QQ,主人在 QQ 里直接对话", mod: null, note: "白名单放行后由 DSH 回复" },
    { k: "bili", name: "B 站机器人", desc: "投稿、稿件管理、评论与数据", mod: null, note: "走无头接口,不抢电脑" },
    { k: "memory", name: "统一记忆库", desc: "跨模块搜索,来源与时间可追", mod: "memory" },
    { k: "home", name: "鱼的小窝", desc: "作息、种地、雇佣与账本", mod: "home" },
    { k: "ws", name: "若的工作区", desc: "对话、模型、工具与任务", mod: "workspace" }
  ];

  function renderQuick() {
    $("quickGrid").innerHTML = QUICK.map(function (q) {
      var st, cls;
      if (q.k === "home" && state.homeLocal) {
        // 小窝现在是 exe 内置引擎,不是外部服务
        st = "本地运行"; cls = "ok";
      } else if (q.k === "qq") {
        // QQ / B站 走本地功能服务,状态直接看服务,不看状态聚合器
        var qq = state.qq;
        if (qq && qq.ws && qq.ws.connected) {
          st = "在线 · " + ((qq.account && qq.account.nickname) || "") + " · 群 " + (qq.groups || 0);
          cls = "ok";
        } else if (qq && qq.napcat && qq.napcat.running) { st = "协议端已启动,等待登录"; cls = "warn"; }
        else { st = "未连接"; cls = "off"; }
      } else if (q.k === "bili") {
        var bl = state.bili;
        if (bl && bl.logged_in) { st = "已登录 · " + (bl.uname || "") + " · Lv" + (bl.level == null ? "-" : bl.level); cls = "ok"; }
        else { st = bl ? "未登录" : "服务未启动"; cls = "off"; }
      } else {
        var m = q.mod ? moduleOf(q.mod) : null;
        if (q.mod) { st = m ? (m.ok ? "在线 · " + (m.latency_ms || 0) + "ms" : "离线") : "未接入";
          cls = m ? (m.ok ? "ok" : "bad") : "off"; }
        else { st = "未接入"; cls = "off"; }
      }
      return '<button class="qcard" data-k="' + q.k + '" title="' + q.desc + '">' +
        '<span class="qico">' + ICONS[q.k] + "</span>" +
        '<span class="qfish"><img src="assets/fatfish.png" alt="" draggable="false"></span>' +
        '<span class="qname">' + q.name + "</span>" +
        '<span class="st ' + cls + '">' + st + "</span>" +
        '<span class="qgo">→</span></button>';
    }).join("");
    var btns = $("quickGrid").querySelectorAll(".qcard");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        var k = this.getAttribute("data-k");
        // 有自己页面的直接进去;没有页面的在右栏展开详情并提示,别点了没反应
        if (k === "home") { switchView("fish"); return; }
        if (k === "memory") { switchView("memory"); return; }
        if (k === "qq") { switchView("qq"); return; }
        if (k === "bili") { switchView("bili"); return; }
        if (k === "ws") { switchView("workspace"); return; }
        renderDetail(k);
        var dc = $("detailCard");
        if (dc) {
          dc.classList.remove("flash"); void dc.offsetWidth; dc.classList.add("flash");
          if (dc.scrollIntoView) dc.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
        var q = null;
        for (var j = 0; j < QUICK.length; j++) if (QUICK[j].k === k) q = QUICK[j];
        toast((q && q.name ? q.name + ":" : "") + ((q && q.note) || "看右栏详情"));
      });
    }
  }

  function renderDetail(k) {
    k = k || "memory";
    var q = null;
    for (var i = 0; i < QUICK.length; i++) if (QUICK[i].k === k) q = QUICK[i];
    if (!q) return;
    var m = q.mod ? moduleOf(q.mod) : null;
    var html = '<div class="qname" style="font-size:14px;margin-bottom:6px">' + q.name + "</div>";
    html += '<p class="hint" style="margin:0 0 8px">' + q.desc + "</p>";
    if (!q.mod) {
      html += '<p class="muted">' + q.note + "。</p>";
    } else if (!m) {
      html += '<p class="muted">状态服务里没有这个模块 —— 说明它现在没起来。</p>';
    } else if (!m.ok) {
      html += '<p class="bad">离线</p><p class="muted">本地服务没响应;起服务后点「刷新」。</p>';
    } else {
      html += '<p class="ok">在线 · ' + (m.latency_ms || 0) + " ms</p>";
      var ov = state.overview || {};
      if (k === "memory" && ov.memory) {
        html += '<p class="mono" style="margin:6px 0">共 ' + ov.memory.total + " 条</p>";
        var bs = ov.memory.by_source || {};
        html += '<p class="muted" style="font-size:12px">' + Object.keys(bs).map(function (s) {
          return s + " " + bs[s];
        }).join(" · ") + "</p>";
      }
      var lh = state.homeLocal;
      if (k === "home" && (lh || ov.home)) {
        var hh = lh || ov.home;
        var names = (hh.characters && hh.characters.length && typeof hh.characters[0] === "object")
          ? hh.characters.map(function (c) { return c.name; }) : (hh.characters || []);
        html += '<p style="margin:6px 0">阶段 ' + esc(hh.phase || "—") + " · 金币 " + (hh.coins == null ? "—" : hh.coins) + "</p>";
        html += '<p class="muted" style="font-size:12px">' + esc(names.join("、") || "—") +
          (hh.tasks_todo == null ? "" : " · 待办 " + hh.tasks_todo + " 条") + " · 本地引擎</p>";
      }
      if (k === "ws") html += '<p class="muted" style="font-size:12px">工作区服务在线;对话与模型在它自己的窗口里。</p>';
    }
    $("detailBody").innerHTML = html;
  }

  function cmdTable(rows) {
    if (!rows.length) return '<p class="muted">还没有下发过命令。</p>';
    var html = '<table class="tbl"><thead><tr><th>ID</th><th>类型</th><th>节点</th><th>状态</th><th>时间</th></tr></thead><tbody>';
    rows.forEach(function (c) {
      html += '<tr><td class="mono">#' + esc(c.id) + "</td><td>" + esc(c.kind) +
        '</td><td class="mono">' + esc(c.node_id) + '</td><td class="' + statusCls(c.status) + '">' +
        esc(statusCn(c.status)) + '</td><td class="mono">' + esc(fmtTime(c.created_at)) + "</td></tr>";
    });
    return html + "</tbody></table>";
  }

  function renderCmds() {
    var recent = (state.cmds || []).slice(0, 5);
    $("cmdCount").textContent = state.cmds.length ? state.cmds.length + " 条" : "还没有";
    $("homeCmds").innerHTML = recent.length
      ? '<table class="tbl"><tbody>' + recent.map(function (c) {
        return '<tr><td class="mono">#' + esc(c.id) + "</td><td>" + esc(c.kind) +
          '</td><td class="' + statusCls(c.status) + '">' + esc(statusCn(c.status)) + "</td></tr>";
      }).join("") + "</tbody></table>"
      : '<p class="muted">还没有下发过命令。</p>';
  }

  function renderNodes() {
    var nodes = state.nodes || [];
    $("nodeCount").textContent = nodes.length ? nodes.length + " 台" : "还没接入";
    if (!nodes.length) {
      $("nodeBody").innerHTML = '<p class="muted">还没有节点登记。Node Agent 跑起来并注册之后,这里会出现。</p>';
      return;
    }
    var html = '<table class="tbl"><thead><tr><th>名称</th><th>平台</th><th>状态</th><th>最后心跳</th></tr></thead><tbody>';
    nodes.forEach(function (n) {
      html += "<tr><td>" + esc(n.hostname || "(未命名)") + '<div class="mono muted">' + esc(n.id) + "</div></td>" +
        "<td>" + esc(n.platform || "—") + "</td>" +
        '<td class="' + (n.online ? "ok" : "muted") + '">' + (n.online ? "在线" : "离线") + "</td>" +
        '<td class="mono">' + esc(fmtTime(n.last_seen)) + "</td></tr>";
    });
    $("nodeBody").innerHTML = html + "</tbody></table>";
  }

  function renderCmdsPage() { $("cmdBody").innerHTML = cmdTable(state.cmds || []); }

  function renderAccount() {
    var u = state.user || {};
    $("accountBody").innerHTML = '<table class="tbl"><tbody>' +
      '<tr><td class="muted">用户名</td><td>' + esc(u.username || "—") + "</td></tr>" +
      '<tr><td class="muted">角色</td><td>' + esc(u.role || "—") + "</td></tr>" +
      '<tr><td class="muted">用户 ID</td><td class="mono">' + esc(u.id || "—") + "</td></tr>" +
      '<tr><td class="muted">服务器</td><td class="mono">' + esc(API_BASE) + "</td></tr>" +
      "</tbody></table>";
  }

  // ---------------- 搜索记忆 ----------------
  function doSearch() {
    var q = $("q").value.trim();
    var out = $("searchOut");
    if (!q) { out.hidden = true; return; }
    out.hidden = false;
    out.innerHTML = '<p class="muted">搜索中…</p>';
    call("memory_search", { q: q }).then(function (res) {
      var items = (res && res.items) || [];
      if (!items.length) { out.innerHTML = '<p class="muted">记忆里没有「' + esc(q) + "」。</p>"; return; }
      out.innerHTML = '<div class="hint" style="margin:0 0 6px">找到 ' + (res.total || items.length) + " 条</div>" +
        items.map(function (it) {
          return '<div class="r"><span class="t">' + esc((it.occurred_at || "").slice(5, 16)) + "</span>" +
            '<span class="s">' + esc(it.source) + "</span><span>" + esc(it.content) + "</span></div>";
        }).join("");
    }).catch(function (e) {
      out.innerHTML = '<p class="bad">' + esc(e.message || e) + "</p>";
    });
  }
  $("btnSearch").addEventListener("click", doSearch);
  $("q").addEventListener("keydown", function (e) { if (e.key === "Enter") doSearch(); });

  // ================= 记忆树(统一记忆库:QQ / B站 / 鱼的小窝 / 工作区) =================
  // 数据是真的(18091 /api/memory/search),分数是按时间算出来的:
  //   普通记忆 5 天掉一半分,置顶 20 天(4 倍慢);≥70 清晰,≥42 正常,<42 模糊。
  //   模糊 = 红心,点它会碎(带合成破碎声);回想一次就重新计时,又变清晰。
  var MEM = {
    items: [], byId: {}, filter: "all", q: "", sel: null, sig: "", ready: false
  };
  var MEM_HL = 5, MEM_HL_PIN = 20, MEM_CLEAR = 70, MEM_FUZZY = 42;
  var NS = "http://www.w3.org/2000/svg";

  var SRC_META = {
    qq: { label: "QQ", color: "#7FA8DC", zh: "QQ 聊天" },
    bili: { label: "B站", color: "#E293B4", zh: "B 站" },
    home: { label: "鱼的小窝", color: "#96C285", zh: "鱼的小窝" },
    workspace: { label: "工作区", color: "#DCC077", zh: "若的工作区" },
    manual: { label: "手动", color: "#A9A9BC", zh: "手动记下" }
  };
  var SRC_ORDER = ["qq", "bili", "home", "workspace", "manual"];
  var RATE_CN = { clear: "清晰", normal: "正常", fuzzy: "模糊" };

  function srcKey(s) {
    s = String(s || "manual").toLowerCase();
    if (s === "farm" || s === "fish") return "home";
    return SRC_META[s] ? s : "manual";
  }
  function memHash(s) {
    var h = 2166136261, str = String(s || "");
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = (h * 16777619) | 0; }
    return Math.abs(h);
  }
  function memFrac(s) { return (memHash(s) % 997) / 997; }
  function memTime(iso) {
    if (!iso) return "—";
    var d = new Date(String(iso).replace(" ", "T"));
    if (isNaN(d.getTime())) return String(iso);
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
      p(d.getHours()) + ":" + p(d.getMinutes());
  }
  // 记忆的发生时间(occurred_at),不是入库时间(created_at)。
  function memDate(it) {
    var base = it.occurred_at || it.created_at;
    var d = base ? new Date(String(base).replace(" ", "T")) : null;
    return (d && !isNaN(d.getTime())) ? d.getTime() : Date.now();
  }
  // 强度 = 时间淡忘(普通 5 天一半,置顶 20 天)+ 回想带来的“变年轻”。
  // 回想一次只能把这段记忆的年龄打 82 折 —— 所以要慢慢想好几次才会变清晰,
  // 不会点一下就从模糊跳到清晰。
  var MEM_RECALL_KEEP = 0.82;   // 每回想一次,年龄 ×0.82
  var MEM_RECALL_CAP = 14;      // 最多累计 14 次,再多也不会更清楚
  function memAgeDays(it) {
    return Math.max(0, (Date.now() - memDate(it)) / 86400000);
  }
  function memRecallN(it) {
    var c = it.context || {};
    return Math.min(MEM_RECALL_CAP, Math.max(0, Number(c.recall_count) || 0));
  }
  function memStrength(it) {
    var c = it.context || {};
    var hl = c.pinned ? MEM_HL_PIN : MEM_HL;
    var ageEff = memAgeDays(it) * Math.pow(MEM_RECALL_KEEP, memRecallN(it));
    var s = 100 * Math.pow(2, -ageEff / hl) + (memFrac(it.id + "j") * 12 - 6);
    return Math.max(0, Math.min(100, s));
  }
  function memRate(s) { return s >= MEM_CLEAR ? "clear" : (s >= MEM_FUZZY ? "normal" : "fuzzy"); }

  function applyMemory(items, force) {
    var sig = items.map(function (it) {
      var c = it.context || {};
      return it.id + ":" + (c.pinned ? 1 : 0) + ":" + (c.recall_at || "");
    }).join("|");
    MEM.items = items.map(function (it) {
      return {
        id: it.id, source: it.source, content: it.content || "", kind: it.kind,
        occurred_at: it.occurred_at, created_at: it.created_at, context: it.context || {}
      };
    });
    MEM.byId = {};
    MEM.items.forEach(function (it) { MEM.byId[it.id] = it; });
    var changed = force || sig !== MEM.sig || !MEM.ready;
    MEM.sig = sig; MEM.ready = true; MEM.err = "";
    renderMemHead();
    if (changed) { renderMemTree(); renderMemCard(); }
  }
  function memFailed(msg) {
    MEM.err = msg || "记忆服务没连上";
    MEM.ready = false;
    if ($("memSum")) $("memSum").textContent = "记忆服务没起来:" + MEM.err;
    renderMemTree();
  }
  function loadMemory(force) {
    return call("memory_search", { q: "", limit: 300 }).then(function (res) {
      applyMemory((res && res.items) || [], force);
    }).catch(function (e) { memFailed(e.message || e); });
  }
  function enterMemory() {
    if (!MEM.ready) { loadMemory(true); return; }
    renderMemHead(); renderMemTree(); renderMemCard();
  }

  function memStats() {
    var c = { all: 0, clear: 0, normal: 0, fuzzy: 0, pinned: 0, avg: 0 };
    var sum = 0;
    MEM.items.forEach(function (it) {
      var s = memStrength(it), r = memRate(s);
      c.all++; c[r]++; sum += s;
      if (it.context && it.context.pinned) c.pinned++;
    });
    c.avg = c.all ? Math.round(sum / c.all) : 0;
    return c;
  }

  function renderMemHead() {
    var sum = $("memSum"); if (!sum) return;
    if (!MEM.ready && !MEM.items.length) {
      sum.textContent = MEM.err ? ("记忆服务没起来:" + MEM.err) : "读取中…";
      return;
    }
    var c = memStats();
    ["cAll", "cClear", "cNormal", "cFuzzy", "cPinned"].forEach(function (id) {
      var el = $(id); if (el) el.textContent = { cAll: c.all, cClear: c.clear, cNormal: c.normal, cFuzzy: c.fuzzy, cPinned: c.pinned }[id];
    });
    var newest = 0;
    MEM.items.forEach(function (it) { var t = memDate(it); if (t > newest) newest = t; });
    var d = newest ? new Date(newest) : null;
    var pad2 = function (n) { return (n < 10 ? "0" : "") + n; };
    sum.innerHTML = "共 <b>" + c.all + "</b> 条 · 平均 <b>" + c.avg + "</b> 分 · 已模糊 <i>" +
      c.fuzzy + "</i> 条" + (d ? " · 最新 " + (d.getMonth() + 1) + "-" + pad2(d.getDate()) : "");
  }

  function heartD(s) {
    var k = s;
    return "M0 " + (k * 0.78).toFixed(2) +
      " C" + (-k * 1.15).toFixed(2) + " " + (k * 0.15).toFixed(2) + " " + (-k * 0.72).toFixed(2) + " " + (-k * 0.92).toFixed(2) +
      " 0 " + (-k * 0.30).toFixed(2) +
      " C" + (k * 0.72).toFixed(2) + " " + (-k * 0.92).toFixed(2) + " " + (k * 1.15).toFixed(2) + " " + (k * 0.15).toFixed(2) +
      " 0 " + (k * 0.78).toFixed(2) + "Z";
  }

  function renderMemTree() {
    var svg = $("memTree"); if (!svg) return;
    var parts = [];
    parts.push('<ellipse class="memGround" cx="500" cy="614" rx="258" ry="15"/>');

    if (!MEM.items.length) {
      parts.push('<text x="500" y="300" text-anchor="middle" fill="currentColor" opacity=".42" font-size="14">' +
        (MEM.ready ? "记忆库还是空的 —— 等 QQ / B站 / 小窝往里写" : ("记忆服务没连上:" + esc(MEM.err || ""))) + "</text>");
      svg.innerHTML = parts.join("");
      renderMemLegend();
      return;
    }

    // 按来源分组(来源是真实字段)
    var groups = {};
    SRC_ORDER.forEach(function (k) { groups[k] = []; });
    MEM.items.forEach(function (it) {
      var k = srcKey(it.source);
      if (!groups[k]) groups[k] = [];
      groups[k].push(it);
    });
    var keys = SRC_ORDER.filter(function (k) { return groups[k] && groups[k].length; });

    var rootY = 604, trunkTop = 512, halfSpan = keys.length > 1 ? 62 : 0;
    var a0 = -90 - halfSpan, a1 = -90 + halfSpan;

    // 树根 + 树干
    parts.push('<path class="memRoots" stroke-width="9" d="M458 610 C480 601 492 589 496 574"/>');
    parts.push('<path class="memRoots" stroke-width="8" d="M542 610 C520 601 508 589 504 574"/>');
    parts.push('<path class="memRoots" stroke-width="6" d="M414 615 C442 607 458 599 468 587"/>');
    parts.push('<path class="memRoots" stroke-width="6" d="M588 615 C560 607 544 599 533 587"/>');
    parts.push('<path class="memTrunk" stroke-width="18" d="M500 ' + rootY + ' C497 570 500 542 500 ' + trunkTop + '"/>');
    parts.push('<path class="memTrunkHi" stroke-width="3" d="M494 592 C492 568 494 542 494 ' + (trunkTop + 8) + '"/>');

    var mid = (keys.length - 1) / 2;
    // 叶子统一大小:所有枝条、所有评级都用同一个半径,向叶子最多的那根枝对齐
    // (鱼的小窝那根枝叶子最多,其它来源跟它一样大,不再按评级放大缩小)
    var maxM = 0;
    keys.forEach(function (k2) { if (groups[k2].length > maxM) maxM = groups[k2].length; });
    var leafR = Math.max(4.2, Math.min(7.4, 7.4 * Math.max(0.66, 18 / (maxM || 1))));
    keys.forEach(function (k, i) {
      var meta = SRC_META[k] || SRC_META.manual;
      var ang = keys.length === 1 ? -90 : a0 + (a1 - a0) * i / (keys.length - 1);
      var rad = ang * Math.PI / 180;
      var len = 312 + 56 * (1 - Math.abs(i - mid) / (mid || 1));
      var p0x = 500, p0y = trunkTop + 2;
      var t1x = p0x + Math.cos(rad) * len, t1y = p0y + Math.sin(rad) * len;
      var cx = p0x + Math.cos(rad) * len * 0.58, cy = p0y + Math.sin(rad) * len * 0.58 - 10;

      function pt(t) {
        var mt = 1 - t;
        return {
          x: mt * mt * p0x + 2 * mt * t * cx + t * t * t1x,
          y: mt * mt * p0y + 2 * mt * t * cy + t * t * t1y
        };
      }
      function tan(t) {
        var dx = 2 * (1 - t) * (cx - p0x) + 2 * t * (t1x - cx);
        var dy = 2 * (1 - t) * (cy - p0y) + 2 * t * (t1y - cy);
        var L = Math.sqrt(dx * dx + dy * dy) || 1;
        return { x: dx / L, y: dy / L };
      }

      parts.push('<path class="memBranch" stroke-width="7" d="M' + p0x + " " + p0y +
        " Q" + cx.toFixed(1) + " " + cy.toFixed(1) + " " + t1x.toFixed(1) + " " + t1y.toFixed(1) + '"/>');

      // 两根小枝,纯装饰,让枝条像树
      [0.52, 0.74].forEach(function (tt, ti) {
        var bp = pt(tt), tg = tan(tt);
        var side = ti ? -1 : 1;
        var ta = Math.atan2(tg.y, tg.x) + side * 0.52;
        var tl = 74 - ti * 12;
        var ex = bp.x + Math.cos(ta) * tl, ey = bp.y + Math.sin(ta) * tl;
        var mx2 = bp.x + Math.cos(ta) * tl * 0.55, my2 = bp.y + Math.sin(ta) * tl * 0.55 - 6;
        parts.push('<path class="memTwig" stroke-width="3.2" d="M' + bp.x.toFixed(1) + " " + bp.y.toFixed(1) +
          " Q" + mx2.toFixed(1) + " " + my2.toFixed(1) + " " + ex.toFixed(1) + " " + ey.toFixed(1) + '"/>');
      });

      // 叶子:弱的靠树干、强的在枝头(按分数排)。每一条记忆都要画出来,不取样、不省略。
      var list = groups[k].slice().sort(function (a, b) { return memStrength(a) - memStrength(b); });
      var m = list.length;

      // 枝条周围的淡色碎光,让树冠不那么空
      for (var d0 = 0; d0 < 8; d0++) {
        var dt = 0.28 + 0.68 * memFrac(k + "dt" + d0);
        var dp = pt(dt), dtg = tan(dt);
        var doff = (memFrac(k + "do" + d0) - 0.5) * 2 * (16 + 22 * memFrac(k + "dw" + d0));
        parts.push('<circle class="memDust" cx="' + (dp.x - dtg.y * doff).toFixed(1) + '" cy="' +
          (dp.y + dtg.x * doff).toFixed(1) + '" r="' + (1.3 + 1.9 * memFrac(k + "dr" + d0)).toFixed(1) +
          '" fill="' + meta.color + '" opacity="' + (0.10 + 0.14 * memFrac(k + "da" + d0)).toFixed(2) + '"/>');
      }

      list.forEach(function (it, j) {
        var t = 0.22 + 0.72 * ((j + 0.5) / m) + (memFrac(it.id + "t") - 0.5) * 0.035;
        var p = pt(t), tg = tan(t);
        var nx = -tg.y, ny = tg.x;
        var side = (j % 3) - 1;
        var off = side * (10 + 25 * memFrac(it.id + "o")) + (memFrac(it.id + "p") - 0.5) * 11;
        var x = p.x + nx * off, y = p.y + ny * off + (memFrac(it.id + "q") - 0.5) * 7;

        var s = memStrength(it), r = memRate(s), ctx = it.context || {};
        var rr = leafR;                 // 所有叶子同一个大小,不按评级、不按枝条条数变
        var cls = "memLeaf " + r + (ctx.pinned ? " pinned" : "");
        var delay = (-memFrac(it.id + "d") * 5.4).toFixed(2);

        parts.push('<g class="' + cls + '" data-mem="' + esc(it.id) + '" transform="translate(' +
          x.toFixed(1) + "," + y.toFixed(1) + ')">');
        parts.push('<circle class="memGlow" r="' + (rr * 2.3).toFixed(1) + '" style="animation-delay:' + delay + 's"/>');
        if (r === "fuzzy") parts.push('<path class="memHeart" d="' + heartD(rr) + '"/>');
        else parts.push('<circle class="memDot" r="' + rr.toFixed(1) + '"/>');
        if (ctx.pinned) parts.push('<circle class="memPin" cx="' + (rr * 1.05).toFixed(1) + '" cy="' +
          (-rr * 1.0).toFixed(1) + '" r="2"/>');
        parts.push('<circle class="memHit" r="' + Math.max(11, rr + 5).toFixed(1) + '"/>');
        parts.push("</g>");
      });

      // 枝头标签
      var lx = t1x + Math.cos(rad) * 14, ly = t1y + Math.sin(rad) * 14;
      var anchor = Math.cos(rad) > 0.28 ? "start" : (Math.cos(rad) < -0.28 ? "end" : "middle");
      parts.push('<text class="memLabel" x="' + lx.toFixed(1) + '" y="' + (ly + 5).toFixed(1) +
        '" text-anchor="' + anchor + '" fill="' + meta.color + '">' + esc(meta.label) + "</text>");
      parts.push('<text class="memLabelN" x="' + lx.toFixed(1) + '" y="' + (ly + 20).toFixed(1) +
        '" text-anchor="' + anchor + '" fill="' + meta.color + '">' + m + " 条</text>");
    });

    // 树干底部一点暖光(记忆的根)
    parts.push('<circle class="memCore" cx="500" cy="' + (trunkTop + 3) + '" r="4.5"/>');

    svg.innerHTML = parts.join("");
    applyMemFilter();
    bindMemTree();
    renderMemLegend();
  }

  function renderMemLegend() {
    var lg = $("memLegend"); if (!lg) return;
    lg.innerHTML =
      '<span><i class="lg-c"></i>清晰</span>' +
      '<span><i class="lg-n"></i>正常</span>' +
      '<span><i class="lg-f"></i>模糊(点一下会响)</span>' +
      '<span><i class="lg-p"></i>置顶</span>';
  }

  function memMatch(it) {
    if (!it) return false;
    var r = memRate(memStrength(it)), f = MEM.filter;
    if (f === "pinned") { if (!it.context.pinned) return false; }
    else if (f !== "all" && r !== f) return false;
    if (MEM.q) {
      var hay = (it.content + " " + it.source + " " + JSON.stringify(it.context || {})).toLowerCase();
      if (hay.indexOf(MEM.q.toLowerCase()) < 0) return false;
    }
    return true;
  }
  function applyMemFilter() {
    var svg = $("memTree"); if (!svg) return;
    var nodes = svg.querySelectorAll(".memLeaf");
    for (var i = 0; i < nodes.length; i++) {
      var id = nodes[i].getAttribute("data-mem");
      nodes[i].classList.toggle("dim", !memMatch(MEM.byId[id]));
      nodes[i].classList.toggle("sel", id === MEM.sel);
    }
  }

  function bindMemTree() {
    var svg = $("memTree");
    if (!svg || svg.__bound) return;
    svg.__bound = true;
    svg.addEventListener("click", function (e) {
      var g = e.target.closest ? e.target.closest("[data-mem]") : null;
      if (!g) return;
      onLeafClick(g.getAttribute("data-mem"));
    });
    svg.addEventListener("mousemove", function (e) {
      var g = e.target.closest ? e.target.closest("[data-mem]") : null;
      if (!g) { hideMemTip(); return; }
      showMemTip(g.getAttribute("data-mem"), e);
    });
    svg.addEventListener("mouseleave", hideMemTip);
  }

  function showMemTip(id, e) {
    var it = MEM.byId[id]; if (!it) return;
    var tip = $("memTip"); if (!tip) return;
    var s = memStrength(it), r = memRate(s), meta = SRC_META[srcKey(it.source)] || SRC_META.manual;
    tip.hidden = false;
    tip.innerHTML = '<div class="tt"><b>' + esc(meta.label) + "</b><span>" + esc(memTime(it.occurred_at)) +
      '</span><span class="pill">' + RATE_CN[r] + " " + Math.round(s) + " 分</span></div>" +
      '<div class="tx">' + esc(it.content) + "</div>";
    var stage = $("memStage"), sr = stage.getBoundingClientRect();
    var x = e.clientX - sr.left + 16, y = e.clientY - sr.top + 14;
    var w = tip.offsetWidth || 240, h = tip.offsetHeight || 70;
    if (x + w > sr.width - 8) x = Math.max(8, x - w - 30);
    if (y + h > sr.height - 8) y = Math.max(8, y - h - 24);
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideMemTip() { var tip = $("memTip"); if (tip) tip.hidden = true; }

  function onLeafClick(id) {
    var it = MEM.byId[id]; if (!it) return;
    MEM.sel = id;
    var r = memRate(memStrength(it));
    renderMemCard();               // 先打开这张记忆的卡片
    applyMemFilter();              // 只更新选中高亮,不重画整棵树
    // 模糊的记忆:点一下只是「碎一下、响一声」的反馈 —— 记忆本身还在树上,
    // 它是随时间淡忘的,不会因为点一下就消失。
    if (r === "fuzzy") shatterLeaf(id);
  }

  function shardPath(sz) {
    return "M0 0 L" + sz.toFixed(1) + " " + (-sz * 0.55).toFixed(1) + " L" + (sz * 0.45).toFixed(1) +
      " " + (sz * 0.85).toFixed(1) + " Z";
  }
  function shatterLeaf(id) {
    var svg = $("memTree"); if (!svg) return;
    var el = svg.querySelector('.memLeaf[data-mem="' + id + '"]');
    var x = 500, y = 300;
    if (el) {
      var m = /translate\(([-\d.]+),([-\d.]+)\)/.exec(el.getAttribute("transform") || "");
      if (m) { x = parseFloat(m[1]); y = parseFloat(m[2]); }
      // 红心自己抖一下、闪一下,但不会消失
      el.classList.remove("cracked");
      void el.getBoundingClientRect();
      el.classList.add("cracked");
      setTimeout(function () { if (el.parentNode) el.classList.remove("cracked"); }, 780);
    }
    var ring = document.createElementNS(NS, "circle");
    ring.setAttribute("class", "memRing");
    ring.setAttribute("cx", x); ring.setAttribute("cy", y); ring.setAttribute("r", 7);
    svg.appendChild(ring);
    for (var i = 0; i < 15; i++) {
      var ang = (i / 15) * Math.PI * 2 + Math.random() * 0.6;
      var dist = 24 + Math.random() * 38;
      var sz = 2.2 + Math.random() * 4.4;
      var g = document.createElementNS(NS, "g");
      g.setAttribute("transform", "translate(" + (x + Math.cos(ang) * 5).toFixed(1) + "," +
        (y + Math.sin(ang) * 5).toFixed(1) + ")");
      var p = document.createElementNS(NS, "path");
      p.setAttribute("class", "shard");
      p.setAttribute("d", shardPath(sz));
      p.style.fill = "var(--mem-fuzzy)";
      p.style.setProperty("--dx", (Math.cos(ang) * dist).toFixed(1) + "px");
      p.style.setProperty("--dy", (Math.sin(ang) * dist + 20).toFixed(1) + "px");
      p.style.setProperty("--rot", (Math.random() * 460 - 230).toFixed(0) + "deg");
      g.appendChild(p);
      svg.appendChild(g);
    }
    playShatter();
    var stage = $("memStage");
    if (stage) { stage.classList.remove("shake"); void stage.offsetWidth; stage.classList.add("shake"); }
    setTimeout(function () {
      var olds = svg.querySelectorAll(".shard,.memRing");
      for (var k = 0; k < olds.length; k++) {
        var host = olds[k].parentNode;
        if (host && host.tagName.toLowerCase() === "g" && host.parentNode === svg) host.remove();
        else olds[k].remove();
      }
    }, 1200);
  }

  function renderMemCard() {
    var card = $("memCard"); if (!card) return;
    var it = MEM.sel ? MEM.byId[MEM.sel] : null;
    if (!it) {
      card.innerHTML = '<div class="mcEmpty"><div class="big">🌿</div>' +
        "<p>点树上的一个光点,看这段记忆。</p>" +
        '<p class="muted">越久越淡:5 天掉一半分。红色的是已经模糊的记忆,点它只是碎一下、响一声,记忆还在树上。</p></div>';
      return;
    }
    var s = memStrength(it), r = memRate(s), ctx = it.context || {};
    var meta = SRC_META[srcKey(it.source)] || SRC_META.manual;
    var html = '<div class="mcTop"><span class="mcPill ' + r + '">' + RATE_CN[r] + "</span>" +
      '<span class="mcScore">' + Math.round(s) + "<small>分</small></span></div>";
    html += '<div class="mcBar"><i class="' + r + '" style="width:' + Math.round(s) + '%"></i></div>';
    html += '<div class="mcMeta"><span><b>' + esc(meta.zh) + "</b></span><span>" +
      esc(memTime(it.occurred_at)) + "</span>" + (ctx.pinned ? "<span>已置顶</span>" : "") +
      (ctx.recall_count ? "<span>回想过 " + esc(ctx.recall_count) + " 次</span>" : "") + "</div>";
    html += '<p class="mcTxt">' + esc(it.content) + "</p>";
    html += '<div class="mcActs">' +
      '<button type="button" id="mcPin">' + (ctx.pinned ? "取消置顶" : "置顶") + "</button>" +
      '<button type="button" id="mcRecall" class="' + (r === "fuzzy" ? "warn" : "") + '">回想一下</button>' +
      '<button type="button" class="ghost" id="mcCopy">复制</button></div>';
    var n = memRecallN(it);
    var nextS = n >= MEM_RECALL_CAP ? s : memStrength({
      id: it.id, occurred_at: it.occurred_at, created_at: it.created_at,
      context: Object.assign({}, ctx, { recall_count: n + 1 })
    });
    var gain = Math.max(1, Math.round(nextS - s));
    html += '<p class="mcNote">' + (n >= MEM_RECALL_CAP
      ? "这条已经想得很清楚了,再多想也不会更清楚。"
      : "回想一次大约 +" + gain + " 分(记忆年轻 18%)—— 要慢慢想好几次才会回到清晰。普通记忆 5 天掉一半分,置顶 20 天。") + "</p>";
    card.innerHTML = html;
    $("mcPin").addEventListener("click", function () { togglePin(it); });
    $("mcRecall").addEventListener("click", function () { recallMem(it); });
    $("mcCopy").addEventListener("click", function () { copyMem(it); });
  }

  function patchCtx(it, ctx) {
    return call("memory_patch", { id: it.id, context: ctx }).then(function () {
      it.context = ctx;
      MEM.sig = "";                       // 让下一次 applyMemory 认为变了
      renderMemHead(); renderMemTree(); renderMemCard();
    });
  }
  function togglePin(it) {
    var ctx = Object.assign({}, it.context || {});
    ctx.pinned = !ctx.pinned;
    var on = ctx.pinned;
    patchCtx(it, ctx).then(function () {
      playChime(on ? 760 : 520);
      toast(on ? "已置顶 —— 它会淡得慢 4 倍" : "已取消置顶");
    }).catch(function (e) { toast("置顶没存上:" + (e.message || e)); });
  }
  function recallMem(it) {
    var before = memStrength(it);
    var n = memRecallN(it);
    if (n >= MEM_RECALL_CAP) { toast("这条已经想得很清楚了,再多想也不会更清楚"); return; }
    var ctx = Object.assign({}, it.context || {});
    ctx.recall_at = new Date().toISOString();
    ctx.recall_count = (Number(ctx.recall_count) || 0) + 1;
    var after = memStrength({
      id: it.id, occurred_at: it.occurred_at, created_at: it.created_at, context: ctx
    });
    patchCtx(it, ctx).then(function () {
      playChime(880);
      renderMemTree(); renderMemCard();
      var d = Math.max(1, Math.round(after - before));
      var left = MEM_CLEAR - Math.round(after);
      toast("想起来了 +" + d + " 分" + (left > 0 ? " —— 再想几次才会清晰" : " —— 又回到清晰了"));
    }).catch(function (e) { toast("回想没存上:" + (e.message || e)); });
  }
  function copyMem(it) {
    var txt = it.content;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(function () { toast("已复制"); },
        function () { toast("复制不了,自己选一下"); });
    } else { toast("复制不了,自己选一下"); }
  }

  // ---------------- 声音(WebAudio 合成,不加载任何音频文件) ----------------
  var AC = null;
  function audioCtx() {
    if (AC) return AC;
    var C = window.AudioContext || window.webkitAudioContext;
    if (!C) return null;
    try { AC = new C(); } catch (e) { AC = null; }
    return AC;
  }
  function playShatter() {
    var ac = audioCtx(); if (!ac) return;
    if (ac.state === "suspended") ac.resume();
    var t0 = ac.currentTime;
    // 玻璃碎裂:高通噪声爆
    var dur = 0.42, sr = ac.sampleRate, len = Math.floor(sr * dur);
    var buf = ac.createBuffer(1, len, sr), d = buf.getChannelData(0);
    for (var i = 0; i < len; i++) {
      var t = i / sr, env = Math.pow(1 - t / dur, 2.3);
      d[i] = (Math.random() * 2 - 1) * env * (1 + 0.45 * Math.sin(t * 260));
    }
    var src = ac.createBufferSource(); src.buffer = buf;
    var hp = ac.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 1500;
    var bp = ac.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 3600; bp.Q.value = 0.7;
    var g = ac.createGain(); g.gain.value = 0.5;
    src.connect(hp); hp.connect(bp); bp.connect(g); g.connect(ac.destination);
    src.start(t0);
    // 高频玻璃余音
    var o = ac.createOscillator(); o.type = "triangle";
    o.frequency.setValueAtTime(2900, t0);
    o.frequency.exponentialRampToValueAtTime(880, t0 + 0.22);
    var og = ac.createGain();
    og.gain.setValueAtTime(0.15, t0);
    og.gain.exponentialRampToValueAtTime(0.001, t0 + 0.3);
    o.connect(og); og.connect(ac.destination); o.start(t0); o.stop(t0 + 0.32);
    // 闷响
    var o2 = ac.createOscillator(); o2.type = "sine";
    o2.frequency.setValueAtTime(150, t0);
    o2.frequency.exponentialRampToValueAtTime(56, t0 + 0.16);
    var g2 = ac.createGain();
    g2.gain.setValueAtTime(0.16, t0);
    g2.gain.exponentialRampToValueAtTime(0.001, t0 + 0.2);
    o2.connect(g2); g2.connect(ac.destination); o2.start(t0); o2.stop(t0 + 0.22);
  }
  function playChime(freq) {
    var ac = audioCtx(); if (!ac) return;
    if (ac.state === "suspended") ac.resume();
    var t0 = ac.currentTime, f = freq || 780;
    [1, 2.02, 3.01].forEach(function (mult, i) {
      var o = ac.createOscillator(); o.type = "sine";
      o.frequency.setValueAtTime(f * mult, t0);
      var g = ac.createGain();
      g.gain.setValueAtTime(0.14 / (i + 1.4), t0);
      g.gain.exponentialRampToValueAtTime(0.0008, t0 + 0.6 + i * 0.1);
      o.connect(g); g.connect(ac.destination);
      o.start(t0); o.stop(t0 + 0.8);
    });
  }

  // ---------------- 轻提示 ----------------
  var toastTimer = null;
  function toast(msg) {
    if (!msg) return;
    var el = document.getElementById("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast"; el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("on"); }, 2600);
  }

  // 记忆页上的筛选 / 搜索
  (function bindMemUI() {
    var chips = $("memChips");
    if (chips) {
      chips.addEventListener("click", function (e) {
        var b = e.target.closest ? e.target.closest("button[data-f]") : null;
        if (!b) return;
        MEM.filter = b.getAttribute("data-f");
        var all = chips.querySelectorAll("button[data-f]");
        for (var i = 0; i < all.length; i++) all[i].classList.toggle("on", all[i] === b);
        applyMemFilter();
      });
    }
    var q = $("memQ");
    function doFind() {
      MEM.q = q ? q.value.trim() : "";
      applyMemFilter();
      if (MEM.q) toast("在 " + MEM.items.length + " 条记忆里筛「" + MEM.q + "」");
    }
    if (q) q.addEventListener("input", function () { MEM.q = q.value.trim(); applyMemFilter(); });
    if (q) q.addEventListener("keydown", function (e) { if (e.key === "Enter") doFind(); });
    if ($("memQBtn")) $("memQBtn").addEventListener("click", doFind);
  })();

  // ================= QQ 机器人 / B站(本地功能服务 18096 / 18097) =================
  // 面板只跟这两个本地服务说话:协议端(NapCat)、B站请求都在服务里跑,
  // 面板里不出现任何第三方窗口 —— 用户看到的始终只有 Rivulet 若。
  var QQ = { recent: [], target: "", timer: null, sending: false };
  var BILI = { videos: [], sel: "", comments: [], loading: false };

  function svcGet(service, path) { return call("svc_get", { service: service, path: path }); }
  function svcPost(service, path, body) {
    return call("svc_post", { service: service, path: path, body: body || {} });
  }

  // ---------------- 左侧状态栏:QQ / B站 ----------------
  function renderSvcRail() {
    var q = state.qq, b = state.bili;
    var qDot = $("lrQqDot"), qTxt = $("lrQqTxt"), bDot = $("lrBiliDot"), bTxt = $("lrBiliTxt");
    if (qDot) {
      var qOn = !!(q && q.ws && q.ws.connected);
      var qPort = !!(q && q.napcat && q.napcat.running);
      qDot.className = "svcDot" + (qOn ? " on" : (qPort ? " warn" : (q ? " bad" : "")));
      if (qTxt) qTxt.textContent = qOn
        ? ((q.account && q.account.nickname) || "在线")
        : (qPort ? "等扫码" : (state.qqErr ? "服务未启动" : "未连接"));
    }
    var pTxt = $("lrPersonaTxt"), wTxt = $("lrWsTxt");
    var pDot = $("lrPersonaDot"), wDot = $("lrWsDot");
    if (pTxt) pTxt.textContent = (state.persona && state.persona.name) || "—";
    if (pDot) pDot.className = "svcDot on";
    var wsOn = !!(state.ws && state.ws.enabled);
    if (wTxt) wTxt.textContent = wsOn ? (state.ws.model || "已接模型") : "未接模型";
    if (wDot) wDot.className = "svcDot" + (wsOn ? " on" : " warn");
    var chipP = $("homePersona");
    if (chipP) chipP.textContent = "人格 " + ((state.persona && state.persona.name) || "—");
    if (bDot) {
      var bOn = !!(b && b.logged_in);
      bDot.className = "svcDot" + (bOn ? " on" : (b ? " bad" : ""));
      if (bTxt) bTxt.textContent = bOn ? (b.uname || "已登录") : (state.biliErr ? "服务未启动" : "未登录");
    }
  }
  function renderMoodRail() {
    var m = state.mood || {};
    var dot = $("lrMoodDot"), txt = $("lrMoodTxt");
    var chip = $("homeMood");
    var word = (m.words && m.words.mood) || (state.moodErr ? "没连上" : "—");
    if (dot) dot.className = "svcDot" + (m.ok ? (m.mood > 0.25 ? " on" : (m.mood < -0.1 ? " warn" : "")) : " bad");
    if (txt) txt.textContent = word;
    if (chip) chip.textContent = "心情 " + word;
  }
  function enterMood() {
    renderMoodPage();
    if (!MOOD.timer) MOOD.timer = setInterval(function () {
      if (state.view !== "mood") return;
      refreshMood();
    }, 8000);
  }
  function refreshMood() {
    svcGet("mood", "/api/state").then(function (m) {
      state.mood = m; state.moodErr = ""; renderMoodRail(); renderMoodPage();
    }).catch(function (e) { state.moodErr = e.message || String(e); renderMoodRail(); renderMoodPage(); });
  }
  function moodBar(name, v, lo, hi, cls, word) {
    var pct = Math.round(Math.max(0, Math.min(1, (v - lo) / (hi - lo))) * 100);
    return '<div class="moodBig"><div class="mh"><span class="mk">' + name + '</span>' +
      '<span class="mw">' + esc(word || "") + "</span>" +
      '<span class="mv">' + Math.round(v * 100) + "</span></div>" +
      '<div class="mb"><i class="' + (cls || "") + '" style="width:' + pct + '%"></i></div></div>';
  }
  function renderMoodPage() {
    var bars = $("moodBars"); if (!bars) return;
    var m = state.mood;
    if (!m) {
      if (bars) bars.innerHTML = '<p class="biliEmpty">心情服务没连上' + (state.moodErr ? ":" + esc(state.moodErr) : "") + "</p>";
      return;
    }
    var w = m.words || {};
    bars.innerHTML =
      moodBar("心情", Number(m.mood || 0), -1, 1, "warm", w.mood) +
      moodBar("精力", Number(m.energy || 0), 0, 1, "cool", w.energy) +
      moodBar("黏人", Number(m.cling || 0), 0, 1, "pink", w.cling) +
      moodBar("委屈", Number(m.hurt || 0), 0, 1, "bad", w.hurt);
    var brief = $("moodBrief");
    if (brief) brief.textContent = (m.brief || "").replace(/【[^】]*】/g, "").trim();
    var notes = $("moodNotes");
    var tag = $("moodNoteTag");
    var list = m.notes || [];
    if (tag) tag.textContent = list.length ? list.length + " 条" : "还没有";
    if (notes) notes.innerHTML = list.length
      ? list.slice().reverse().map(function (n) { return '<div class="moodNoteRow">' + esc(n) + "</div>"; }).join("")
      : '<p class="biliEmpty">还没有记下什么。</p>';
    var scope = $("moodScope");
    if (scope) {
      scope.innerHTML = '<div class="svcKv"><span class="k">QQ</span><span class="v">说话影响心情;回复语气跟着变</span></div>' +
        '<div class="svcKv"><span class="k">B站</span><span class="v">刷推荐/写评论时带当前心情</span></div>' +
        '<div class="svcKv"><span class="k">工作区</span><span class="v">对话同样读这份心情</span></div>';
    }
    var sub = $("moodSub");
    if (sub) sub.textContent = "全局一份 —— QQ / B站 / 工作区共用同一份心情 · 更新 " + (m.ts ? "" : "");
  }
  (function bindMood() {
    var r = $("moodRefresh");
    if (r) r.addEventListener("click", function () { refreshMood(); toast("已刷新心情"); });
    var adj = $("moodAdjust");
    if (adj) adj.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-adj]") : null;
      if (!b) return;
      var v = b.getAttribute("data-adj").split(":");
      var body = {}; body[v[0]] = Number(v[1]);
      svcPost("mood", "/api/adjust", body).then(function (m) {
        state.mood = m; renderMoodRail(); renderMoodPage();
      }).catch(function (err) { toast("调不动:" + (err.message || err)); });
    });
    var chip = $("homeMood");
    if (chip) chip.addEventListener("click", function () { switchView("mood"); });
  })();
  (function bindRail() {
    [["lrQq", "qq"], ["lrBili", "bili"], ["lrMood", "mood"], ["lrPersona", "persona"], ["lrWs", "workspace"], ["lrSetup", "setup"]].forEach(function (pair) {
      var el = $(pair[0]);
      if (el) el.addEventListener("click", function () { switchView(pair[1]); });
    });
  })();

  // ---------------- QQ 机器人(管理台:DSH 接入 QQ,不在这里聊天) ----------------
  function enterQq() {
    refreshQqStatus();
    loadWhitelist();
    loadQqConversation();
    loadQqAudit();
    loadQqConfig();
    bindQqQuick();
    if (!QQ.timer) {
      QQ.timer = setInterval(function () {
        if (state.view !== "qq") return;
        refreshQqStatus(); loadQqConversation(); loadQqAudit();
      }, 6000);
    }
  }
  // 通用分页:任何 .qqTabs / .pgTabs 里带 data-pane 的按钮,切换同级 .qqPanes / .pgPanes
  function initPaneTabs() {
    var groups = document.querySelectorAll(".qqTabs, .pgTabs");
    for (var i = 0; i < groups.length; i++) {
      var tabs = groups[i];
      if (tabs.__paneBound || tabs.__bound) continue;
      tabs.__paneBound = true;
      (function (tabs) {
        var page = tabs.closest ? tabs.closest(".page") : null;
        var paneWrap = tabs.parentNode.querySelector(".qqPanes, .pgPanes");
        if (!paneWrap) return;
        var key = page ? "ruo.pane." + page.id : "";
        function activate(k) {
          var bs = tabs.querySelectorAll("button[data-pane]");
          for (var j = 0; j < bs.length; j++) bs[j].classList.toggle("on", bs[j].getAttribute("data-pane") === k);
          var ps = paneWrap.querySelectorAll(".qqPane, .pgPane");
          for (var m = 0; m < ps.length; m++) {
            var on = ps[m].getAttribute("data-pane") === k;
            if (ps[m].parentNode === paneWrap || true) ps[m].classList.toggle("on", on);
          }
          if (key) { try { localStorage.setItem(key, k); } catch (e) { } }
        }
        tabs.addEventListener("click", function (e) {
          var b = e.target.closest ? e.target.closest("button[data-pane]") : null;
          if (!b) return;
          activate(b.getAttribute("data-pane"));
          if (window.__glassSelectAll) setTimeout(function () { window.__glassSelectAll(); }, 0);
        });
        if (key) {
          try {
            var saved = localStorage.getItem(key);
            if (saved && tabs.querySelector('button[data-pane="' + saved + '"]')) activate(saved);
          } catch (e) { }
        }
      })(tabs);
    }
  }
  window.__initPaneTabs = initPaneTabs;

  // 玻璃下拉:原生 select 的弹层是系统绘制的,无法改色 → 自绘一层,
  // 原生 select 只作为数据载体(.value / change 都照旧)
  function glassSelect(sel) {
    if (!sel || sel.__glass) return;
    sel.__glass = true;
    sel.classList.add("gsel-native");
    var wrap = document.createElement("div");
    wrap.className = "gsel";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "gselBtn";
    btn.innerHTML = '<span class="gselTxt">' + (sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : "") + '</span>' +
      '<svg viewBox="0 0 24 24" fill="none"><path d="m7 10 5 5 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    var menu = document.createElement("div");
    menu.className = "gselMenu";
    menu.hidden = true;
    [].forEach.call(sel.options, function (o) {
      var b = document.createElement("button");
      b.type = "button";
      b.setAttribute("data-v", o.value);
      b.textContent = o.text;
      if (o.value === sel.value) b.classList.add("on");
      b.addEventListener("click", function () {
        sel.value = o.value;
        sync();
        try { sel.dispatchEvent(new Event("change", { bubbles: true })); } catch (e) { }
        menu.hidden = true;
        btn.classList.remove("open");
      });
      menu.appendChild(b);
    });
    function sync() {
      var cur = sel.options[sel.selectedIndex];
      btn.querySelector(".gselTxt").textContent = cur ? cur.text : "";
      [].forEach.call(menu.children, function (b) {
        b.classList.toggle("on", b.getAttribute("data-v") === sel.value);
      });
    }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = menu.hidden;
      document.querySelectorAll(".gselMenu").forEach(function (m) { m.hidden = true; });
      document.querySelectorAll(".gselBtn").forEach(function (b) { b.classList.remove("open"); });
      menu.hidden = !open;
      btn.classList.toggle("open", open);
    });
    document.addEventListener("click", function () { menu.hidden = true; btn.classList.remove("open"); });
    // 先按原生 select 的实际宽度/伸缩性定尺寸,再把它藏起来
    var rc = sel.getBoundingClientRect();
    var cs = window.getComputedStyle(sel);
    if (parseFloat(cs.flexGrow) > 0) { wrap.style.flex = "1 1 auto"; wrap.style.minWidth = "104px"; }
    else if (rc.width > 0) { wrap.style.width = Math.round(rc.width) + "px"; }
    sel.parentNode.insertBefore(wrap, sel.nextSibling);
    wrap.appendChild(btn);
    wrap.appendChild(menu);
    sel.__sync = sync;
    sync();
  }
  // 全站铺开:登录后与每次切页时把页面里的原生 select 都换成玻璃下拉
  function glassSelectAll(root) {
    var scope = root || document;
    var list = scope.querySelectorAll(".page select:not(.gsel-native)");
    for (var i = 0; i < list.length; i++) glassSelect(list[i]);
  }
  window.__glassSelectAll = glassSelectAll;
  function syncGlassSelects() {
    var k = $("wlKind");
    if (k && k.__sync) k.__sync();
  }

  // 右栏「快捷操作」:转调页头/主区的真实按钮,避免出现两套逻辑
  function bindQqQuick() {
    function relay(id, target) {
      var b = $(id); if (!b || b.__bound) return;
      b.__bound = true;
      b.addEventListener("click", function () {
        var t = $(target); if (t) t.click();
      });
    }
    relay("qqSyncConfig", "qqReload");
    // 顶部胶囊页签 = 分页切换
    var tabs = $("qqTabs");
    if (tabs && !tabs.__bound) {
      tabs.__bound = true;
      tabs.addEventListener("click", function (e) {
        var b = e.target.closest ? e.target.closest("button[data-pane]") : null;
        if (!b) return;
        var key = b.getAttribute("data-pane");
        var all = tabs.querySelectorAll("button[data-pane]");
        for (var i = 0; i < all.length; i++) all[i].classList.toggle("on", all[i] === b);
        var panes = document.querySelectorAll("#qqPanes .qqPane");
        for (var j = 0; j < panes.length; j++) {
          panes[j].classList.toggle("on", panes[j].getAttribute("data-pane") === key);
        }
        try { localStorage.setItem("ruo.qqPane", key); } catch (err) { }
      });
      // 记住上次看的页
      try {
        var saved = localStorage.getItem("ruo.qqPane");
        if (saved) {
          var btn = tabs.querySelector('button[data-pane="' + saved + '"]');
          if (btn) btn.click();
        }
      } catch (err) { }
    }
    // 对话 / 审计 的独立刷新
    var rc = $("qqQReloadChat");
    if (rc && !rc.__bound) {
      rc.__bound = true;
      rc.addEventListener("click", function () { loadQqConversation(); toast("对话已刷新"); });
    }
    var au = $("qqQAudit");
    if (au && !au.__bound) {
      au.__bound = true;
      au.addEventListener("click", function () { loadQqAudit(); toast("审计日志已刷新"); });
    }
    // 白名单子页签:QQ 号 / QQ 群
    glassSelect($("wlKind"));
    var wt = $("qqWlTabs");
    if (wt && !wt.__bound) {
      wt.__bound = true;
      wt.addEventListener("click", function (e) {
        var b = e.target.closest ? e.target.closest("button[data-wl]") : null;
        if (!b) return;
        QQ.wlTab = b.getAttribute("data-wl");
        var k = $("wlKind"); if (k) k.value = QQ.wlTab;
        syncGlassSelects();
        renderWhitelist();
      });
    }
  }
  function refreshQqStatus() {
    svcGet("qq", "/api/status").then(function (s) {
      state.qq = s; state.qqErr = ""; renderQqStatus(); renderSvcRail();
    }).catch(function (e) {
      state.qq = null; state.qqErr = e.message || String(e); renderQqStatus(); renderSvcRail();
    });
    loadQqExtras();
  }
  function loadQqExtras() {
    svcGet("qq", "/api/mood").then(function (r) { QQ.mood = r || {}; renderQqStats(); }).catch(function () {});
    svcGet("qq", "/api/idle").then(function (r) { QQ.idle = r || {}; renderQqStats(); }).catch(function () {});
    svcGet("qq", "/api/qzone").then(function (r) { QQ.qz = r || {}; renderQqStats(); }).catch(function () {});
  }
  function bindQqExtras() {
    var bS = $("idleSave");
    if (bS) bS.addEventListener("click", function () {
      var body = {
        daily_max: parseInt(($("idleMax") || {}).value, 10) || 6,
        min_gap_min: parseInt(($("idleMin") || {}).value, 10) || 40,
        max_gap_min: parseInt(($("idleMaxGap") || {}).value, 10) || 100,
        quiet_from: parseInt(($("idleQf") || {}).value, 10),
        quiet_to: parseInt(($("idleQt") || {}).value, 10)
      };
      svcPost("qq", "/api/idle/config", body).then(function () {
        toast("主动消息设置已保存"); loadQqExtras();
      }).catch(function (e) { toast("保存失败:" + (e.message || e)); });
    });
    var b1 = $("idleNow");
    if (b1) b1.addEventListener("click", function () {
      b1.disabled = true;
      svcPost("qq", "/api/idle/trigger", {}).then(function (r) {
        toast(r && r.ok ? ("已经主动发了:" + (r.text || "")) : ((r && r.error) || "没发出去"));
        loadQqExtras();
      }).catch(function (e) { toast("失败:" + (e.message || e)); })
        .then(function () { b1.disabled = false; });
    });
    var bQ = $("qzSave");
    if (bQ) bQ.addEventListener("click", function () {
      var body = {
        enabled: !!($("qzAuto") || {}).checked,
        per_day: parseInt(($("qzPerDay") || {}).value, 10) || 1,
        from_hour: parseInt(($("qzFrom") || {}).value, 10),
        to_hour: parseInt(($("qzTo") || {}).value, 10),
        min_gap_min: parseInt(($("qzGap") || {}).value, 10) || 240
      };
      svcPost("qq", "/api/qzone/config", body).then(function () {
        toast("说说设置已保存"); loadQqExtras();
      }).catch(function (e) { toast("保存失败:" + (e.message || e)); });
    });
    var b2 = $("qzNow");
    if (b2) b2.addEventListener("click", function () {
      b2.disabled = true;
      b2.textContent = "发送中…";
      svcPost("qq", "/api/qzone/post", {}).then(function (r) {
        if (r && r.ok) toast("说说发出去了:" + (r.text || ""));
        else toast("说说没发成:" + ((r && (r.error || r.msg)) || ""));
        loadQqExtras();
      }).catch(function (e) { toast("失败:" + (e.message || e)); })
        .then(function () { b2.disabled = false; b2.textContent = "发一条说说"; });
    });
    var b3 = $("qzAuto");
    if (b3) b3.addEventListener("change", function () {
      var on = this.checked;
      svcPost("qq", "/api/qzone/config", { enabled: on }).then(function () {
        toast(on ? "自动说说已开" : "自动说说已关"); loadQqExtras();
      }).catch(function () {});
    });
    var b4 = $("idleAuto");
    if (b4) b4.addEventListener("change", function () {
      var on = this.checked;
      svcPost("qq", "/api/idle/config", { enabled: on }).then(function () {
        toast(on ? "主动消息已开" : "主动消息已关"); loadQqExtras();
      }).catch(function () {});
    });
  }
  function renderQqStatus() {
    var q = state.qq;
    var dot = $("qqDot"), name = $("qqName"), sub = $("qqSub");
    if (!q) {
      if (dot) dot.className = "svcDot big bad";
      if (name) name.textContent = "QQ 机器人";
      if (sub) sub.textContent = "本地 QQ 服务没起来" + (state.qqErr ? ":" + state.qqErr : "");
      renderQqStats();
      return;
    }
    var on = q.ws && q.ws.connected;
    if (dot) dot.className = "svcDot big " + (on ? "on" : (q.napcat && q.napcat.running ? "warn" : "bad"));
    if (name) name.textContent = on ? ("QQ 机器人 · " + ((q.account && q.account.nickname) || "")) : "QQ 机器人";
    var rd = $("qqRunDot"), rt = $("qqRunText");
    if (rd) rd.className = "svcDot " + (on ? "on" : ((q.napcat && q.napcat.running) ? "warn" : "bad"));
    if (rt) rt.textContent = on ? "运行中" : ((q.napcat && q.napcat.running) ? "协议端已起 · 未连上" : "未启动");
    var sb = $("qqStart"); if (sb) sb.textContent = (q.napcat && q.napcat.running) ? "重启" : "启动";
    if (sub) {
      sub.textContent = on
        ? (q.last_send_error
          ? ("已接入 QQ,但发不出去:" + q.last_send_error)
          : ("DSH 已接入 QQ · " + ((q.account && q.account.uin) || "") + " · 群 " + (q.groups || 0) + " 个"))
        : (q.napcat && q.napcat.running ? "协议端在跑,连接还没建立(点「重连」)" : "协议端未启动 —— 点「启动 QQ 协议端」");
    }
    var rep = $("qqReplyOn"); if (rep) rep.checked = !!q.reply_enabled;
    var wlOn = $("qqWlOn"); if (wlOn) wlOn.checked = !!(q.whitelist && q.whitelist.enabled);
    renderQqStats();
  }
  function renderQqStats() {
    var q = state.qq || {};
    var st = q.stats || {};
    var acc = q.account || {};
    var wl = q.whitelist || {};
    var rows = [
      ["机器人", acc.nickname ? (acc.nickname + " · " + acc.uin) : "—"],
      ["QQ 连接", (q.ws && q.ws.connected) ? "在线" : "离线"],
      ["协议端", (q.napcat && q.napcat.running) ? "在跑" : "未启动"],
      ["群 / 好友", (q.groups || 0) + " / " + (q.friends || 0)],
      ["DSH 模型", (q.model || "—") + (q.has_key ? "" : " · 没配 key")],
      ["放行 / 拦截 / 回复", (st.allowed || 0) + " / " + (st.blocked || 0) + " / " + (st.replied || 0)],
      ["发送状态", q.last_send_error ? ("失败:" + q.last_send_error) : (st.replied ? "正常" : "—")],
      ["白名单", (wl.enabled ? "启用" : "停用") + " · " + (wl.users || 0) + " 人 / " + (wl.groups || 0) + " 群"]
    ];
    var rowsBox = $("qqStatRows");
    if (rowsBox) rowsBox.innerHTML = rows.map(function (r) {
      return '<div class="qqKv"><span class="k">' + r[0] + '</span><span class="v">' + esc(r[1]) + "</span></div>";
    }).join("");

    // 扫码 / 协议端提示
    var np = q.napcat || {};
    var qr = "";
    if (np.state === "qrcode" && np.qr_b64) {
      qr = '<div class="qrBox"><div class="qrT">手机 QQ 扫码登录</div>' +
        '<img alt="QQ 登录二维码" src="data:image/png;base64,' + np.qr_b64 + '">' +
        '<div class="qrHint">' + esc(np.hint || "") + "</div></div>";
    } else if (np.state === "scanned") {
      qr = '<div class="qrBox"><div class="qrT">已扫码</div><div class="qrHint">' +
        esc(np.hint || "手机上点「确认登录」") + "</div></div>";
    } else if (np.state === "error") {
      qr = '<div class="qrBox"><div class="qrT bad">协议端出错</div><div class="qrHint">' +
        esc(np.hint || "") + "</div></div>";
    }
    var qrBox = $("qqQr"); if (qrBox) qrBox.innerHTML = qr;

    // 主动消息
    var idle = (QQ.idle || {});
    var ic = idle.config || {};
    var idleBox = $("qqIdleBox");
    if (idleBox) idleBox.innerHTML =
      '<div class="qqKv"><span class="k">开关</span><span class="v"><label class="svcSwitch"><input type="checkbox" id="idleAuto"' +
      (ic.enabled ? " checked" : "") + '><span>开</span></label></span>' +
      '<span class="k" style="margin-left:auto">今天</span><span class="v">' + (idle.today_count || 0) + " / " + (ic.daily_max || 6) + "</span></div>" +
      '<div class="cfgRow">' +
      '<label>每天<input id="idleMax" type="number" min="1" max="20" value="' + (ic.daily_max || 6) + '">次</label>' +
      '<label>间隔<input id="idleMin" type="number" min="5" max="600" value="' + (ic.min_gap_min || 40) + '">~<input id="idleMaxGap" type="number" min="5" max="600" value="' + (ic.max_gap_min || 100) + '">分</label>' +
      '<label>安静<input id="idleQf" type="number" min="0" max="23" value="' + (ic.quiet_from == null ? 23 : ic.quiet_from) + '">~<input id="idleQt" type="number" min="0" max="23" value="' + (ic.quiet_to == null ? 9 : ic.quiet_to) + '">点</label>' +
      '</div>' +
      '<div class="svcActs"><button type="button" class="btn ghost sm" id="idleSave">保存</button>' +
      '<button type="button" class="btn ghost sm" id="idleNow">现在就找他</button></div>' +
      '<div class="qqKv"><span class="k">下次</span><span class="v">' + (idle.next_at ? esc(idle.next_at) : (ic.enabled ? "排期中" : "未开")) + "</span></div>";

    // QQ 空间说说
    var qz = (QQ.qz || {});
    var qc = qz.config || {};
    var qzBox = $("qqQzBox");
    if (qzBox) qzBox.innerHTML =
      '<div class="qqKv"><span class="k">自动发说说</span><span class="v"><label class="svcSwitch"><input type="checkbox" id="qzAuto"' +
      (qc.enabled ? " checked" : "") + '><span>每天自动</span></label></span></div>' +
      '<div class="cfgRow">' +
      '<label>每天<input id="qzPerDay" type="number" min="1" max="5" value="' + (qc.per_day || 1) + '">条</label>' +
      '<label>时段<input id="qzFrom" type="number" min="0" max="23" value="' + (qc.from_hour == null ? 10 : qc.from_hour) + '">~<input id="qzTo" type="number" min="0" max="23" value="' + (qc.to_hour == null ? 22 : qc.to_hour) + '">点</label>' +
      '<label>间隔<input id="qzGap" type="number" min="30" max="1440" value="' + (qc.min_gap_min || 240) + '">分</label>' +
      '</div>' +
      '<div class="svcActs"><button type="button" class="btn ghost sm" id="qzSave">保存</button>' +
      '<button type="button" class="btn ghost sm" id="qzNow">马上发一条</button></div>' +
      '<div class="qqKv"><span class="k">上次</span><span class="v">' + esc((qz.state && qz.state.last_at) || "还没发过") + "</span></div>";

    bindQqExtras();
  }
  function loadWhitelist() {
    svcGet("qq", "/api/whitelist").then(function (w) {
      QQ.wl = w || {}; renderWhitelist();
    }).catch(function (e) { toast("读白名单失败:" + (e.message || e)); });
  }
  function renderWhitelist() {
    var box = $("wlList"); if (!box) return;
    var w = QQ.wl || {};
    var tag = $("qqWlTag");
    var users = w.users || [], groups = w.groups || [];
    if (tag) tag.textContent = users.length + " 人 / " + groups.length + " 群";
    var on = $("qqWlOn"); if (on) on.checked = !!w.enabled;

    // 子页签:QQ 号 / QQ 群
    var tab = QQ.wlTab === "group" ? "group" : "user";
    var tu = $("qqWlTabUser"), tg = $("qqWlTabGroup");
    if (tu) tu.classList.toggle("on", tab === "user");
    if (tg) tg.classList.toggle("on", tab === "group");
    var cu = $("wlUserCount"), cg = $("wlGroupCount");
    if (cu) cu.textContent = users.length;
    if (cg) cg.textContent = groups.length;

    var list = tab === "group" ? groups : users;
    if (!list.length) {
      box.innerHTML = '<p class="biliEmpty">' +
        (tab === "group" ? "还没有群被放行 —— 加一个群号,群里 @她 就会回。"
                         : "白名单是空的 —— 先把你自己的 QQ 号加进来,否则 DSH 不会回任何人。") + "</p>";
      return;
    }
    function row(kind, x) {
      var raw = (x.note || "").trim();
      var nm = raw || (kind === "user" ? "未备注的 QQ" : "未备注的群");
      // 备注本身就叫「主人」时不再重复加标签
      var badge = (/主人|owner/i.test(raw) && raw !== "主人") ? '<i class="wlTag">主人</i>' : "";
      return '<div class="wlRow">' +
        '<span class="wlAva">' + esc(nm.slice(0, 1)) + "</span>" +
        '<span class="wlInfo"><b>' + esc(nm) + badge + "</b>" +
        '<small>' + (kind === "user" ? "QQ 号" : "群号") + " · <em>" + esc(x.id) + "</em></small></span>" +
        '<button type="button" class="wlDel" data-del="' + kind + ":" + esc(x.id) + '" title="移除">' +
        '<svg viewBox="0 0 24 24" fill="none"><path d="M5 7h14M9.5 7V5.4h5V7M7 7l.8 12h8.4L17 7M10.4 10.5v5.5M13.6 10.5v5.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div>';
    }
    box.innerHTML = (tab === "group" ? groups : users).map(function (x) {
      return row(tab === "group" ? "group" : "user", x);
    }).join("");
    var btns = box.querySelectorAll("button[data-del]");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        var v = this.getAttribute("data-del").split(":");
        wlRemove(v[0], v[1]);
      });
    }
  }
  function wlAdd() {
    var kind = $("wlKind") ? $("wlKind").value : "user";
    var id = $("wlId") ? $("wlId").value.trim() : "";
    var note = $("wlNote") ? $("wlNote").value.trim() : "";
    if (!/^\d+$/.test(id)) { toast("只能填数字 QQ 号 / 群号"); return; }
    svcPost("qq", "/api/whitelist/add", { kind: kind, id: id, note: note }).then(function (r) {
      if (r && r.ok) {
        if ($("wlId")) $("wlId").value = "";
        if ($("wlNote")) $("wlNote").value = "";
        toast(r.duplicate ? "已经在白名单里了" : "已加入白名单");
        loadWhitelist(); refreshQqStatus();
      } else toast((r && r.error) || "加入失败");
    }).catch(function (e) { toast("加入失败:" + (e.message || e)); });
  }
  function wlRemove(kind, id) {
    svcPost("qq", "/api/whitelist/remove", { kind: kind, id: id }).then(function () {
      toast("已移除"); loadWhitelist(); refreshQqStatus();
    }).catch(function (e) { toast("移除失败:" + (e.message || e)); });
  }
  function loadQqConfig() {
    svcGet("qq", "/api/config").then(function (r) {
      var c = (r && r.config) || {};
      if ($("cfgWs")) $("cfgWs").value = c.ws_url || "";
      if ($("cfgAccount")) $("cfgAccount").value = c.account || "";
      if ($("cfgModel")) $("cfgModel").value = c.model || "";
      if ($("cfgKey")) $("cfgKey").placeholder = c.has_key ? "已配置(留空不改)" : "没配,填一个";
    }).catch(function () {});
  }
  function saveQqConfig() {
    var body = {
      ws_url: $("cfgWs") ? $("cfgWs").value.trim() : "",
      account: $("cfgAccount") ? $("cfgAccount").value.trim() : "",
      model: $("cfgModel") ? $("cfgModel").value.trim() : ""
    };
    var k = $("cfgKey") ? $("cfgKey").value.trim() : "";
    if (k) body.api_key = k;
    svcPost("qq", "/api/config", body).then(function (r) {
      if (r && r.ok) {
        toast("配置已保存");
        if ($("cfgKey")) $("cfgKey").value = "";
        loadQqConfig(); refreshQqStatus();
      } else toast((r && r.error) || "保存失败");
    }).catch(function (e) { toast("保存失败:" + (e.message || e)); });
  }
  function loadQqConversation() {
    svcGet("qq", "/api/conversation?limit=60").then(function (r) {
      QQ.conv = (r && r.items) || []; renderQqConversation();
    }).catch(function () {});
  }
  function renderQqConversation() {
    var box = $("qqConv"); if (!box) return;
    var items = QQ.conv || [];
    var tag = $("qqConvTag"); if (tag) tag.textContent = items.length ? items.length + " 条" : "还没有";
    if (!items.length) {
      box.innerHTML = '<p class="biliEmpty">还没有对话。你在 QQ 里给机器人发一句,这里会同时显示你说了什么、DSH 回了什么。</p>';
      return;
    }
    var atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    box.innerHTML = items.map(function (m) {
      var out = m.decision === "reply";
      var err = m.decision === "error";
      var who = out ? "DSH" : (m.sender_name || m.sender || "?");
      return '<div class="convMsg ' + (out ? "out" : (err ? "err" : "in")) + '">' +
        '<div class="ch"><b>' + esc(who) + '</b><span>' + esc(String(m.time || "").slice(5, 16)) + "</span>" +
        (err ? '<span class="bad">失败</span>' : "") + "</div>" +
        '<div class="cb">' + esc(err ? (m.reason || "发送失败") : (m.text || "")) + "</div></div>";
    }).join("");
    if (atBottom) box.scrollTop = box.scrollHeight;
  }
  function loadQqAudit() {
    svcGet("qq", "/api/audit?limit=60").then(function (r) {
      QQ.audit = (r && r.items) || []; renderQqAudit();
    }).catch(function () {});
  }
  function renderQqAudit() {
    var box = $("qqAudit"); if (!box) return;
    var items = (QQ.audit || []).slice().reverse();
    var tag = $("qqAuditTag"); if (tag) tag.textContent = items.length ? items.length + " 条" : "还没有";
    if (!items.length) {
      box.innerHTML = '<p class="biliEmpty">还没有消息经过网关。你在 QQ 里给机器人发一句,这里就会出现放行 / 拦截记录。</p>';
      return;
    }
    box.innerHTML = items.map(function (m) {
      var cls = m.decision === "allow" ? "ok" : (m.decision === "block" ? "bad" : "warn");
      var word = m.decision === "allow" ? "放行" : (m.decision === "block" ? "拦截" : "错误");
      return '<div class="auditRow"><span class="mono">' + esc(String(m.time || "").slice(5, 16)) + "</span>" +
        '<span class="' + cls + '">' + word + "</span>" +
        '<span class="who">' + esc(m.sender_name || m.sender) + "</span>" +
        '<span class="txt">' + esc(m.text || m.reason || "") + "</span></div>";
    }).join("");
  }
  function qqStart() {
    toast("正在请求启动 QQ 协议端(会弹 UAC,请点「是」)…");
    svcPost("qq", "/api/napcat/start", {}).then(function (res) {
      if (res && res.ok) {
        toast(res.already ? "协议端已经在跑了" : (res.hint || "已请求启动"));
        var n = 0;
        var t = setInterval(function () { n++; refreshQqStatus(); if (n > 20) clearInterval(t); }, 3000);
      } else toast((res && res.error) || "启动失败");
    }).catch(function (e) { toast("启动失败:" + (e.message || e)); });
  }
  (function bindQq() {
    if ($("wlAddBtn")) $("wlAddBtn").addEventListener("click", wlAdd);
    if ($("wlId")) $("wlId").addEventListener("keydown", function (e) { if (e.key === "Enter") wlAdd(); });
    if ($("qqStart")) $("qqStart").addEventListener("click", qqStart);
    if ($("cfgSave")) $("cfgSave").addEventListener("click", saveQqConfig);
    if ($("qqReload")) $("qqReload").addEventListener("click", function () {
      svcPost("qq", "/api/reload", {}).then(function () { refreshQqStatus(); toast("已重连"); })
        .catch(function (e) { toast("重连失败:" + (e.message || e)); });
    });
    if ($("qqReplyOn")) $("qqReplyOn").addEventListener("change", function () {
      var on = this.checked;
      svcPost("qq", "/api/config", { reply_enabled: on }).then(function () {
        toast(on ? "DSH 自动回复已开" : "DSH 自动回复已关"); refreshQqStatus();
      }).catch(function (e) { toast("改不了:" + (e.message || e)); });
    });
    if ($("qqWlOn")) $("qqWlOn").addEventListener("change", function () {
      var on = this.checked;
      svcPost("qq", "/api/whitelist/enabled", { on: on }).then(function () {
        toast(on ? "白名单已启用" : "白名单已停用(所有消息都会被拦)");
        loadWhitelist(); refreshQqStatus();
      }).catch(function (e) { toast("改不了:" + (e.message || e)); });
    });
  })();

  // ---------------- B 站(原版能力:投稿 / 稿件管理 / 评论) ----------------
  var MOOD = { timer: null };
  var BILI = { videos: [], sel: "", comments: [], loading: false, partitions: [],
               file: "", cover: "", pubTimer: null, editing: "" };
  function enterBili() {
    renderBiliStatus();
    loadBiliDaily();
    if (!BILI.partitions.length) loadBiliPartitions();
    if (!BILI.videos.length) loadBiliVideos(); else renderBiliVideos();
  }
  function renderBiliStatus() {
    var b = state.bili;
    var name = $("biliName"), sub = $("biliSub"), face = $("biliFace");
    if (!b) {
      if (name) name.textContent = "B 站机器人";
      if (sub) sub.textContent = "本地 B站 服务没起来" + (state.biliErr ? ":" + state.biliErr : "");
      renderBiliStats();
      return;
    }
    if (name) name.textContent = b.logged_in ? (b.uname || "B 站机器人") : "B 站机器人";
    if (sub) sub.textContent = b.logged_in
      ? ("已登录 · Lv" + (b.level == null ? "-" : b.level) + " · 硬币 " + (b.coins == null ? "—" : b.coins) + (b.allow_write ? " · 可投稿/回复" : " · 只读(写操作没开)"))
      : ("未登录:" + (b.error || "cookie 过期"));
    if (face) {
      if (b.face) { face.src = b.face; face.style.display = ""; } else { face.style.display = "none"; }
    }
    renderBiliStats();
  }
  function renderBiliStats() {
    var box = $("biliStats"); if (!box) return;
    var b = state.bili || {};
    var rows = [
      ["账号", b.uname || "—"],
      ["UID", b.mid || "—"],
      ["等级", b.level == null ? "—" : "Lv" + b.level],
      ["硬币", b.coins == null ? "—" : b.coins],
      ["大会员", b.vip ? "是" : "否"],
      ["写权限", b.allow_write ? "已开(投稿/回复)" : "只读"],
      ["稿件", BILI.videos.length ? BILI.videos.length + " 条" : "—"]
    ];
    var html = rows.map(function (r) {
      return '<div class="svcKv"><span class="k">' + r[0] + '</span><span class="v' +
        (r[0] === "UID" ? " mono" : "") + '">' + esc(r[1]) + "</span></div>";
    }).join("");
    var d = BILI.daily || {};
    var dc = (BILI.dailyCfg && BILI.dailyCfg.config) || {};
    var dHtml = 
      '<div class="svcKv"><span class="k">状态</span><span class="v">' +
      (d.running ? "正在刷…" : (d.finished ? ("上次 " + esc(d.finished)) : "还没刷过")) + "</span></div>" +
      '<div class="cfgRow"><label class="svcSwitch"><input type="checkbox" id="dailyAuto"' +
      (dc.enabled ? " checked" : "") + '><span>每天自动刷</span></label>' +
      '<label>每天<input id="dailyPerDay" type="number" min="1" max="6" value="' + (dc.per_day || 1) + '">次</label>' +
      '<label>时段<input id="dailyFrom" type="number" min="0" max="23" value="' + (dc.from_hour == null ? 10 : dc.from_hour) + '">~<input id="dailyTo" type="number" min="0" max="23" value="' + (dc.to_hour == null ? 22 : dc.to_hour) + '">点</label></div>' +
      '<div class="cfgRow">' +
      '<label>刷几条<input id="dailyCount" type="number" min="1" max="10" value="' + (BILI.dailyCount || 3) + '"></label>' +
      '<label class="svcSwitch"><input type="checkbox" id="dailyLike"' + (BILI.dailyLike === false ? "" : " checked") + '><span>点赞</span></label>' +
      '<label class="svcSwitch"><input type="checkbox" id="dailyCmt"' + (BILI.dailyCmt === false ? "" : " checked") + '><span>评论</span></label>' +
      '<label class="svcSwitch"><input type="checkbox" id="dailyNotify"' + (BILI.dailyNotify === false ? "" : " checked") + '><span>通知我</span></label>' +
      '</div>' +
      '<div class="svcActs"><button type="button" class="ghost mini" id="dailyGo">开始刷</button></div>' +
      '<pre class="pubLog" id="dailyLog">' + esc(((d.log || []).slice(-8)).join("\n")) + "</pre>";
    box.innerHTML = html;
    var dbox = $("biliDaily");
    if (dbox) dbox.innerHTML = dHtml;
    var da = $("dailyAuto");
    if (da) da.addEventListener("change", function () {
      var on = this.checked;
      svcPost("bili", "/api/daily/config", {
        enabled: on,
        per_day: parseInt(($("dailyPerDay") || {}).value, 10) || 1,
        from_hour: parseInt(($("dailyFrom") || {}).value, 10),
        to_hour: parseInt(($("dailyTo") || {}).value, 10),
        count: BILI.dailyCount || 3
      }).then(function () { toast(on ? "每天自动刷推荐已开" : "自动刷推荐已关"); loadBiliDaily(); })
        .catch(function (e) { toast("保存失败:" + (e.message || e)); });
    });
    var g = $("dailyGo");
    if (g) g.addEventListener("click", function () {
      g.disabled = true; g.textContent = "刷着呢…";
      BILI.dailyCount = parseInt(($("dailyCount") || {}).value, 10) || 3;
      BILI.dailyLike = !!($("dailyLike") || {}).checked;
      BILI.dailyCmt = !!($("dailyCmt") || {}).checked;
      BILI.dailyNotify = !!($("dailyNotify") || {}).checked;
      svcPost("bili", "/api/daily/watch", { count: BILI.dailyCount, auto_like: BILI.dailyLike,
        auto_comment: BILI.dailyCmt, notify: BILI.dailyNotify }).then(function (r) {
        if (!(r && r.ok)) toast((r && r.error) || "启动失败");
        if (!BILI.dailyTimer) BILI.dailyTimer = setInterval(loadBiliDaily, 2500);
        loadBiliDaily();
      }).catch(function (e) { toast("失败:" + (e.message || e)); })
        .then(function () { g.disabled = false; g.textContent = "开始刷"; });
    });
  }
  function loadBiliDaily() {
    svcGet("bili", "/api/daily/config").then(function (c) {
      BILI.dailyCfg = c || {}; renderBiliStats();
    }).catch(function () {});
    svcGet("bili", "/api/daily/status").then(function (r) {
      BILI.daily = r || {};
      var log = $("dailyLog");
      if (log) { log.textContent = ((r && r.log) || []).slice(-8).join("\n"); log.scrollTop = log.scrollHeight; }
      if (r && !r.running && BILI.dailyTimer) { clearInterval(BILI.dailyTimer); BILI.dailyTimer = null; }
      renderBiliStats();
    }).catch(function () {});
  }
  function loadBiliPartitions() {
    svcGet("bili", "/api/partitions").then(function (r) {
      BILI.partitions = (r && r.items) || [];
      var sel = $("pubTid"); if (!sel) return;
      sel.innerHTML = BILI.partitions.map(function (p) {
        return '<option value="' + p.tid + '">' + esc(p.name) + "</option>";
      }).join("");
    }).catch(function () {});
  }
  function loadBiliVideos() {
    if (BILI.loading) return;
    BILI.loading = true;
    svcGet("bili", "/api/videos?limit=12").then(function (res) {
      BILI.videos = (res && res.items) || [];
      BILI.err = (res && res.error) || "";
      renderBiliVideos(); renderBiliStats();
      if (BILI.videos.length && !BILI.sel) selectBiliVideo(BILI.videos[0].bvid);
    }).catch(function (e) {
      BILI.err = e.message || String(e);
      var box = $("biliVids");
      if (box) box.innerHTML = '<p class="biliEmpty">读不到稿件:' + esc(BILI.err) + "</p>";
    }).then(function () { BILI.loading = false; });
  }
  function renderBiliVideos() {
    var box = $("biliVids"); if (!box) return;
    var tag = $("biliVidTag"); if (tag) tag.textContent = BILI.videos.length ? BILI.videos.length + " 条" : "—";
    if (!BILI.videos.length) {
      box.innerHTML = '<p class="biliEmpty">' + (BILI.err ? esc(BILI.err) : "还没有稿件") + "</p>";
      return;
    }
    box.innerHTML = BILI.videos.map(function (v) {
      var on = v.bvid === BILI.sel;
      var html = '<div class="biliVid' + (on ? " on" : "") + '" data-bvid="' + esc(v.bvid) + '">' +
        '<div class="t">' + esc(v.title) + "</div>" +
        '<div class="m"><span>' + esc(v.bvid) + "</span><span>播放 " + (v.play == null ? "—" : v.play) +
        "</span><span>评论 " + (v.comment == null ? "—" : v.comment) + "</span><span>" + esc(v.created || "") + "</span></div>";
      if (on) {
        html += '<div class="editBox">' +
          '<input class="editTitle" placeholder="新标题(留空不改)" value="">' +
          '<input class="editDesc" placeholder="新简介(留空不改)">' +
          '<div class="svcActs"><button type="button" class="ghost mini editSave">保存修改</button>' +
          '<span class="editMsg"></span></div></div>';
      }
      return html + "</div>";
    }).join("");
    var cards = box.querySelectorAll(".biliVid");
    for (var i = 0; i < cards.length; i++) {
      cards[i].addEventListener("click", function (e) {
        if (e.target.closest && e.target.closest(".editBox")) return;
        selectBiliVideo(this.getAttribute("data-bvid"));
      });
    }
    var saves = box.querySelectorAll(".editSave");
    for (var j = 0; j < saves.length; j++) {
      saves[j].addEventListener("click", function (e) {
        e.stopPropagation();
        var card = this.closest(".biliVid");
        var bvid = card.getAttribute("data-bvid");
        var title = card.querySelector(".editTitle").value.trim();
        var desc = card.querySelector(".editDesc").value.trim();
        var msg = card.querySelector(".editMsg");
        if (!title && !desc) { msg.textContent = "标题/简介至少填一个"; return; }
        msg.textContent = "保存中…";
        svcPost("bili", "/api/edit", { bvid: bvid, title: title, desc: desc }).then(function (r) {
          if (r && r.ok) { msg.textContent = "已保存"; loadBiliVideos(); }
          else msg.textContent = (r && r.error) || "保存失败";
        }).catch(function (err) { msg.textContent = "保存失败:" + (err.message || err); });
      });
    }
  }
  function selectBiliVideo(bvid) {
    if (!bvid) return;
    BILI.sel = bvid;
    renderBiliVideos();
    loadBiliComments(bvid);
  }
  function loadBiliComments(bvid) {
    var box = $("biliCmts");
    if (box) box.innerHTML = '<p class="biliEmpty">读评论中…</p>';
    svcGet("bili", "/api/comments?bvid=" + encodeURIComponent(bvid) + "&limit=20").then(function (res) {
      BILI.comments = (res && res.items) || [];
      BILI.cmtErr = (res && res.error) || "";
      renderBiliComments();
    }).catch(function (e) { BILI.cmtErr = e.message || String(e); renderBiliComments(); });
  }
  function renderBiliComments() {
    var box = $("biliCmts"); if (!box) return;
    var tag = $("biliCmtTag");
    if (tag) tag.textContent = BILI.comments.length ? BILI.comments.length + " 条" : "—";
    if (!BILI.comments.length) {
      box.innerHTML = '<p class="biliEmpty">' + (BILI.cmtErr ? esc(BILI.cmtErr) : "这条视频还没有评论") + "</p>";
      return;
    }
    box.innerHTML = BILI.comments.map(function (c) {
      return '<div class="biliCmt"><div class="u"><b>' + esc(c.user || "?") + "</b><span>" +
        esc(c.ctime || "") + "</span><span>赞 " + (c.like || 0) + "</span></div>" +
        '<div class="c">' + esc(c.text || "") + "</div></div>";
    }).join("");
  }
  function biliReply() {
    var inp = $("biliReply"), text = inp ? inp.value.trim() : "";
    if (!BILI.sel) { toast("先在中间选一条稿件"); return; }
    if (!text) { toast("写点什么再回"); return; }
    svcPost("bili", "/api/reply", { bvid: BILI.sel, text: text }).then(function (res) {
      if (res && res.ok) { if (inp) inp.value = ""; toast("回复已发出"); loadBiliComments(BILI.sel); }
      else toast((res && res.error) || "回复失败");
    }).catch(function (e) { toast("回复失败:" + (e.message || e)); });
  }
  function pubPickVideo() {
    call("pick_file", { kind: "video" }).then(function (p) {
      if (!p) return;
      BILI.file = p;
      var name = p.split(/[\\/]/).pop();
      if ($("pubFile")) $("pubFile").textContent = name;
      if ($("pubTitle") && !$("pubTitle").value.trim()) {
        $("pubTitle").value = name.replace(/\.[^.]+$/, "");
      }
    }).catch(function (e) { toast("选文件失败:" + (e.message || e)); });
  }
  function pubPickCover() {
    call("pick_file", { kind: "image" }).then(function (p) {
      if (!p) return;
      BILI.cover = p;
      if ($("pubCoverTxt")) $("pubCoverTxt").textContent = p.split(/[\\/]/).pop();
      svcGet("bili", "/api/cover?path=" + encodeURIComponent(p)).then(function (r) {
        var img = $("pubCoverImg");
        if (img && r && r.ok) { img.src = "data:image/" + (r.mime || "png") + ";base64," + r.b64; img.hidden = false; }
      }).catch(function () {});
    }).catch(function (e) { toast("选封面失败:" + (e.message || e)); });
  }
  function pubStart(dry) {
    if (!BILI.file) { toast("先选视频文件"); return; }
    if (!$("pubTitle") || !$("pubTitle").value.trim()) { toast("标题不能空"); return; }
    var body = {
      file: BILI.file,
      title: $("pubTitle").value.trim(),
      desc: $("pubDesc") ? $("pubDesc").value.trim() : "",
      tag: $("pubTag") ? $("pubTag").value.trim() : "",
      tid: $("pubTid") ? parseInt($("pubTid").value, 10) : 188,
      cover: BILI.cover || "",
      is_only_self: $("pubSelf") && $("pubSelf").checked,
      dry: !!dry
    };
    var tip = $("pubTip");
    if (!dry) {
      if (tip) tip.textContent = "提交后这条就真发出去了 —— 再点一次「上传并发布」确认。";
      if (!BILI.confirmGo) { BILI.confirmGo = true; return; }
      BILI.confirmGo = false;
    }
    if ($("pubLog")) $("pubLog").textContent = "";
    svcPost("bili", "/api/publish", body).then(function (r) {
      if (r && r.ok) {
        toast(dry ? "开始上传(只传不提交)" : "开始上传并发布");
        if (tip) tip.textContent = dry ? "只上传:文件传到 B站,不提交。" : "正在上传…";
        if (!BILI.pubTimer) BILI.pubTimer = setInterval(pubPoll, 1200);
        pubPoll();
      } else toast((r && r.error) || "启动失败");
    }).catch(function (e) { toast("启动失败:" + (e.message || e)); });
  }
  function pubPoll() {
    svcGet("bili", "/api/publish_status").then(function (r) {
      if (!r) return;
      var bar = $("pubBar"); if (bar) bar.style.width = (r.pct || 0) + "%";
      var log = $("pubLog");
      if (log) { log.textContent = (r.log || []).join("\n"); log.scrollTop = log.scrollHeight; }
      if (!r.running) {
        if (BILI.pubTimer) { clearInterval(BILI.pubTimer); BILI.pubTimer = null; }
        if (r.ok_result) {
          toast(r.dry ? "上传完成(还没提交)" : "投稿成功");
          if ($("pubTip")) $("pubTip").textContent = r.dry ? "已上传,可用「上传并发布」继续提交。" : "投稿成功。";
          loadBiliVideos();
        } else if (r.error) {
          toast("投稿失败:" + r.error);
          if ($("pubTip")) $("pubTip").textContent = "失败原因见下面日志。";
        }
      }
    }).catch(function () {});
  }
  (function bindBili() {
    if ($("biliReplyBtn")) $("biliReplyBtn").addEventListener("click", biliReply);
    if ($("biliReply")) $("biliReply").addEventListener("keydown", function (e) { if (e.key === "Enter") biliReply(); });
    if ($("pubVideo")) $("pubVideo").addEventListener("click", pubPickVideo);
    if ($("pubCover")) $("pubCover").addEventListener("click", pubPickCover);
    if ($("pubDry")) $("pubDry").addEventListener("click", function () { pubStart(true); });
    if ($("pubGo")) $("pubGo").addEventListener("click", function () { pubStart(false); });
    if ($("biliReload")) $("biliReload").addEventListener("click", function () {
      BILI.videos = []; BILI.comments = []; BILI.sel = "";
      svcPost("bili", "/api/reload", {}).then(function (s) {
        state.bili = s; renderBiliStatus(); renderSvcRail(); loadBiliVideos(); toast("已刷新");
      }).catch(function (e) { toast("刷新失败:" + (e.message || e)); });
    });
  })();

  // ================= 人格 / 工作区(独立模块) =================
  var PERSONA = { data: null, fields: [], channels: [], ch: "", presets: [] };
  var WS = { conv: "", msgs: [], convs: [], tasks: [], models: [], sending: false };

  function svcGet(service, path) { return call("svc_get", { service: service, path: path }); }
  function svcPost(service, path, body) {
    return call("svc_post", { service: service, path: path, body: body || {} });
  }

  // ---------------- 人格 ----------------
  var PERSONA = { text: "", ch: "qq", chans: { qq: "", bili: "", workspace: "" }, presets: [], saved: "" };

  function enterPersona() {
    svcGet("persona", "/api/persona").then(function (r) {
      var d = (r && r.data) || {};
      PERSONA.text = d.text || "";
      PERSONA.chans = Object.assign({ qq: "", bili: "", workspace: "" }, d.channels || {});
      PERSONA.saved = PERSONA.text;
      PERSONA.savedAt = d.saved_at || "";
      fillPersonaForm();
      loadPersonaPresets();
    }).catch(function (e) {
      var box = $("personaText");
      if (box) { box.value = ""; box.placeholder = "人格服务没起来:" + (e.message || e); }
    });
  }
  function fillPersonaForm() {
    var ta = $("personaText");
    if (ta) ta.value = PERSONA.text;
    var ch = $("personaChText");
    if (ch) ch.value = PERSONA.chans[PERSONA.ch] || "";
    var tabs = $("personaChTabs");
    if (tabs) {
      var bs = tabs.querySelectorAll("button[data-ch]");
      for (var i = 0; i < bs.length; i++) bs[i].classList.toggle("on", bs[i].getAttribute("data-ch") === PERSONA.ch);
    }
    updatePersonaFoot();
  }
  function updatePersonaFoot() {
    var ta = $("personaText");
    var n = ta ? ta.value.length : 0;
    var c = $("personaCount");
    if (c) c.textContent = n + " 字";
    var st = $("personaState");
    if (st) {
      var dirty = ta && ta.value !== PERSONA.saved;
      st.textContent = dirty ? "有改动没保存" : (PERSONA.savedAt ? "上次保存 " + PERSONA.savedAt : "");
      st.className = dirty ? "dirty" : "";
    }
  }
  function savePersona() {
    var ta = $("personaText");
    var ch = $("personaChText");
    var chans = Object.assign({}, PERSONA.chans);
    if (ch) chans[PERSONA.ch] = ch.value;
    var text = ta ? ta.value : "";
    if (!text.trim()) { toast("一个字都没有,那就没脾气了"); return; }
    svcPost("persona", "/api/persona", { text: text, channels: chans }).then(function (r) {
      PERSONA.text = (r && r.data && r.data.text) || text;
      PERSONA.chans = (r && r.data && r.data.channels) || chans;
      PERSONA.saved = PERSONA.text;
      PERSONA.savedAt = (r && r.data && r.data.saved_at) || "";
      updatePersonaFoot();
      toast("人格存好了 —— 已清空对话上下文,下一句话按新人格来");
    }).catch(function (e) { toast("保存失败:" + (e.message || e)); });
  }
  function loadPersonaPresets() {
    svcGet("persona", "/api/persona/presets").then(function (r) {
      PERSONA.presets = (r && r.items) || [];
      var box = $("personaPresets");
      if (!box) return;
      box.innerHTML = PERSONA.presets.map(function (p, i) {
        return '<button type="button" class="ghost mini" data-preset="' + i + '">' + esc(p.name) + "</button>";
      }).join("");
      var bs = box.querySelectorAll("button[data-preset]");
      for (var i = 0; i < bs.length; i++) {
        bs[i].addEventListener("click", function () {
          var p = PERSONA.presets[parseInt(this.getAttribute("data-preset"), 10)];
          if (!p) return;
          var ta = $("personaText");
          if (ta) { ta.value = p.text; }
          updatePersonaFoot();
          toast("已套用模板「" + p.name + "」,记得点保存");
        });
      }
    }).catch(function () {});
  }
  (function bindPersona() {
    var ta = $("personaText");
    if (ta) ta.addEventListener("input", updatePersonaFoot);
    var ch = $("personaChText");
    var tabs = $("personaChTabs");
    if (tabs) tabs.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-ch]") : null;
      if (!b) return;
      if (ch) PERSONA.chans[PERSONA.ch] = ch.value;
      PERSONA.ch = b.getAttribute("data-ch");
      if (ch) ch.value = PERSONA.chans[PERSONA.ch] || "";
      var bs = tabs.querySelectorAll("button[data-ch]");
      for (var i = 0; i < bs.length; i++) bs[i].classList.toggle("on", bs[i].getAttribute("data-ch") === PERSONA.ch);
      var nm = { qq: "QQ", bili: "B站", workspace: "工作区" }[PERSONA.ch] || PERSONA.ch;
      var hint = $("personaChHint");
      if (hint) hint.textContent = "这一栏只影响「" + nm + "」,不影响另外两处;留空就跟左边那份一样。";
    });
    var s = $("personaSave"); if (s) s.addEventListener("click", savePersona);
    var u = $("personaUndo"); if (u) u.addEventListener("click", function () {
      svcPost("persona", "/api/persona/undo", {}).then(function (r) {
        PERSONA.text = (r && r.data && r.data.text) || PERSONA.text;
        PERSONA.chans = (r && r.data && r.data.channels) || PERSONA.chans;
        PERSONA.saved = PERSONA.text;
        fillPersonaForm();
        toast("已撤回上一版");
      }).catch(function (e) { toast("没撤上:" + ((e && e.message) || "这是第一次改,还没有上一版")); });
    });
    var r = $("personaReset"); if (r) r.addEventListener("click", function () {
      svcPost("persona", "/api/persona/reset", {}).then(function (res) {
        PERSONA.text = (res && res.data && res.data.text) || "";
        PERSONA.chans = (res && res.data && res.data.channels) || PERSONA.chans;
        PERSONA.saved = PERSONA.text;
        fillPersonaForm();
        toast("已恢复默认人格(记得点保存)");
      }).catch(function (e) { toast("恢复失败:" + (e.message || e)); });
    });
    var go = $("personaAskGo");
    if (go) go.addEventListener("click", function () {
      var ask = $("personaAsk");
      var want = ask ? ask.value.trim() : "";
      var st = $("personaAskState");
      if (!want) { toast("先说说你想要什么样的人格,比如「叫我宝宝,说话分几条发」"); return; }
      go.disabled = true;
      if (st) st.textContent = "正在让工作区写…";
      var sys = "你是人格设定助手。用户会用大白话描述他希望 AI 助手是什么样的人。" +
        "请把这段描述写成一份人格设定文本,要求:" +
        "\n1) 用第二人称「你」,写给这个 AI 自己看;" +
        "\n2) 分小节(称呼 / 怎么说话 / 语气 / 不要做什么 / 举例),每条一行;" +
        "\n3) 用户说的每个要求都要保留,不要加戏、不要扩写;" +
        "\n4) 直接输出人格文本本身,不要前言后语、不要解释。";
      svcPost("workspace", "/api/generate", { system: sys, prompt: "我想要的人格:" + want }).then(function (r) {
        if (r && r.ok && r.text) {
          var ta = $("personaText");
          if (ta) ta.value = r.text.trim();
          updatePersonaFoot();
          if (st) st.textContent = "写好了 —— 检查一下,没问题点右上角「保存」";
          toast("工作区写好了,看一眼再保存");
        } else {
          var msg = (r && r.error) || "没写出来";
          if (st) st.textContent = "没写出来:" + msg;
          toast("没写出来:" + wsErrText(msg));
        }
      }).catch(function (e) {
        if (st) st.textContent = "没写出来:" + (e.message || e);
        toast("没写出来:" + wsErrText(e.message || e));
      }).then(function () { go.disabled = false; });
    });
    var qa = $("personaQuickAdd");
    if (qa) qa.addEventListener("click", function () {
      var inp = $("personaQuick"), ta = $("personaText");
      var line = inp ? inp.value.trim() : "";
      if (!line) { toast("先写一句你想让她怎么改,比如「说话短一点」"); return; }
      if (ta) {
        var base = ta.value.replace(/\s+$/, "");
        ta.value = base + (base ? "\n" : "") + line;
        updatePersonaFoot();
      }
      if (inp) inp.value = "";
      toast("已加到正文末尾,记得点「保存」");
    });
    var chip = $("homePersona");
    if (chip) chip.addEventListener("click", function () { switchView("persona"); });
  })();

  // ---------------- 工作区 ----------------
  function enterWorkspace() {
    loadWsModel();
    loadWsConvs();
    loadWsTasks();
    loadWsOverview();
    if (WS.conv) loadWsMessages(WS.conv);
  }
  function loadWsModel() {
    svcGet("workspace", "/api/models/status").then(function (st) {
      WS.modelStatus = st || {};
      var dot = $("wsDot"), sub = $("wsSub");
      if (dot) dot.className = "svcDot big " + (st && st.enabled ? "on" : "warn");
      if (sub) sub.textContent = st && st.enabled
        ? ("模型 " + (st.model || "") + " · " + (st.provider || ""))
        : "还没启用模型 —— 在右边填一个";
      renderWsSide();
    }).catch(function () {});
    svcGet("workspace", "/api/models").then(function (r) {
      WS.models = (r && r.items) || [];
      var cur = WS.models.filter(function (m) { return m.enabled; })[0];
      if (cur) {
        if ($("wsProvider")) $("wsProvider").value = cur.provider || "deepseek";
        if ($("wsModel")) $("wsModel").value = cur.model || "";
        if ($("wsBase")) $("wsBase").value = cur.base_url || "";
      }
      renderWsSide();
    }).catch(function () {});
  }
  function renderWsSide() {
    var box = $("wsSide"); if (!box) return;
    var st = WS.modelStatus || {};
    var stat = $("wsModelStatus");
    if (stat) stat.innerHTML =
      '<div class="svcKv"><span class="k">状态</span><span class="v ' + (st.enabled ? "ok" : "warn") + '">' +
      (st.enabled ? "已启用" : "未启用") + "</span></div>" +
      '<div class="svcKv"><span class="k">模型</span><span class="v">' + esc(st.model || "—") + "</span></div>" +
      '<div class="svcKv"><span class="k">服务商</span><span class="v">' + esc(st.provider || "—") + "</span></div>" +
      '<div class="svcKv"><span class="k">Key</span><span class="v">' + (st.has_key ? "已配置" : "没配") + "</span></div>";
    box.innerHTML = '<div class="wsConvs" id="wsConvs"></div>';
    var list = $("wsConvs");
    if (list) {
      if (!WS.convs.length) list.innerHTML = '<p class="biliEmpty">还没有会话</p>';
      else list.innerHTML = WS.convs.slice(0, 30).map(function (c) {
        return '<div class="wsConv' + (c.id === WS.conv ? " on" : "") + '" data-id="' + esc(c.id) + '">' +
          '<b>' + esc(c.title || "(无标题)") + "</b><small>" + esc(c.updated_at || c.created_at || "") + "</small></div>";
      }).join("");
      var items = list.querySelectorAll(".wsConv");
      for (var i = 0; i < items.length; i++) {
        items[i].addEventListener("click", function () {
          WS.conv = this.getAttribute("data-id");
          var inp0 = $("wsInput"); if (inp0) inp0.value = "";
          loadWsMessages(WS.conv);
          renderWsSide();
          var dr = $("wsDrawer"); if (dr) dr.hidden = true;
        });
      }
    }
    renderWsPanel();
  }

  // 工作区右侧面板:模型状态 / 运行状态 / 当前会话名(全部真实数据)
  function loadWsOverview() {
    call("overview").then(function (r) { WS.ov = r || {}; renderWsPanel(); }).catch(function () {});
    call("nodes_list").then(function (r) { WS.nodes = r || {}; renderWsPanel(); }).catch(function () {});
  }
  function renderWsPanel() {
    var st = WS.modelStatus || {};
    var name = st.model || "未启用模型";
    var desc = st.enabled ? ("已启用 · " + (st.provider || "自定义")) : "还没有启用模型，点右侧箭头配置";
    var mn = $("wsModelName"); if (mn) mn.textContent = name;
    var md = $("wsModelDesc"); if (md) md.textContent = desc;
    var chip = $("wsChipModel"); if (chip) chip.textContent = name;
    var dot = document.querySelector(".wsModelDot"); if (dot) dot.className = "wsModelDot" + (st.enabled ? "" : " off");
    // 会话数量 tag
    var ct = $("wsConvTag"); if (ct) ct.textContent = WS.convs.length ? (WS.convs.length + " 个") : "—";
    // 会话数量 tag
    var ct = $("wsConvTag"); if (ct) ct.textContent = WS.convs.length ? (WS.convs.length + " 个") : "—";
    // 当前会话名
    var cn = $("wsConvName");
    if (cn) {
      var cur = null;
      for (var i = 0; i < WS.convs.length; i++) if (WS.convs[i].id === WS.conv) cur = WS.convs[i];
      cn.textContent = cur && cur.title ? cur.title : "新对话";
    }
    // 运行状态
    var ov = WS.ov || {}, mods = ov.modules || [];
    var coreOk = true, online = 0;
    mods.forEach(function (m) {
      if (m.ok) online++;
      if ((m.name === "core" || m.name === "memory" || m.name === "workspace") && !m.ok) coreOk = false;
    });
    var rs = $("wsRunState");
    if (rs) {
      rs.className = "wsRunState" + (coreOk ? "" : " bad");
      rs.innerHTML = "<i></i><span>" + (mods.length ? (coreOk ? "运行正常" : "部分服务离线") : "读取中…") + "</span>";
    }
    var ro = $("wsRunOnline"); if (ro) ro.textContent = mods.length ? (online + " / " + mods.length + " 在线") : "—";
    var nd = WS.nodes && WS.nodes.nodes ? WS.nodes.nodes : [];
    var nOn = nd.filter(function (n) { return n.online; }).length;
    var rn = $("wsRunNodes"); if (rn) rn.textContent = nd.length ? (nOn + " / " + nd.length + " 在线") : "—";
    var rm = $("wsRunMem"); if (rm) rm.textContent = (ov.memory && ov.memory.total != null) ? (ov.memory.total + " 条") : "—";
    // sparkline:真实服务延迟
    var sp = $("wsSpark");
    if (sp) {
      var vals = mods.map(function (m) { return Math.max(0, Math.min(2400, m.latency_ms || 0)); });
      if (vals.length >= 2) {
        var W = 234, H = 56, max = Math.max.apply(null, vals.concat([1]));
        var pts = vals.map(function (v, i) {
          var x = 6 + i * (W - 12) / (vals.length - 1);
          var y = H - 8 - (v / max) * (H - 18);
          return [x, y];
        });
        var line = pts.map(function (q) { return q[0].toFixed(1) + "," + q[1].toFixed(1); }).join(" ");
        var poly = sp.querySelector("polyline"); if (poly) poly.setAttribute("points", line);
        var area = sp.querySelector(".area");
        if (area) area.setAttribute("d", "M" + pts[0][0].toFixed(1) + " " + (H - 8) + " L" + line.replace(/ /g, " L") + " L" + pts[pts.length - 1][0].toFixed(1) + " " + (H - 8) + " Z");
      }
    }
  }
  function loadWsConvs() {
    svcGet("workspace", "/api/conversations").then(function (r) {
      WS.convs = (r && r.items) || [];
      // 参考图:进入工作区默认是「新对话」欢迎态,历史会话在左上角会话抽屉里
      renderWsSide();
      if (WS.conv) loadWsMessages(WS.conv); else renderWsMsgs();
    }).catch(function () {});
  }
  function loadWsMessages(id) {
    svcGet("workspace", "/api/conversations/" + encodeURIComponent(id)).then(function (r) {
      WS.msgs = (r && r.messages) || [];
      renderWsMsgs();
    }).catch(function () {});
  }
  function renderWsMsgs() {
    var box = $("wsMsgs"); if (!box) return;
    var tag = $("wsConvTag"); if (tag) tag.textContent = WS.msgs.length ? WS.msgs.length + " 条" : "—";
    if (!WS.msgs.length) {
      var ava = window.RivuletAvatar ? window.RivuletAvatar.html({ size: "lg" }) : "";
      box.innerHTML = '<div class="wsWelcome">' +
        '<div class="wsWelcomeRow"><span class="convAva">' + ava + '</span>' +
        '<div class="wsWelcomeBubble">你好呀～<br>我是若！(≧▽≦)<br>有什么想和我聊的吗？<br>无论是问题、想法，还是摸鱼，<br>我都会一直在哦～ ♡</div></div>' +
        '<div class="wsChips">' +
        '<button type="button" data-suggest="帮我写点东西"><svg viewBox="0 0 24 24" fill="none"><path d="M4 20l4-1 10-10a2.1 2.1 0 0 0-3-3L5 16l-1 4Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>帮我写点东西</button>' +
        '<button type="button" data-suggest="帮我分析一下这个问题"><svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="6.5" stroke="currentColor" stroke-width="1.6"/><path d="m16 16 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>分析一下这个问题</button>' +
        '<button type="button" data-suggest="今日运势如何？"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3.5 14.6 9l6 .6-4.5 4 1.3 5.9L12 16.7 6.6 19.5 7.9 13.6 3.4 9.6l6-.6L12 3.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>今日运势如何？</button>' +
        '<button type="button" data-suggest="陪我聊聊天"><svg viewBox="0 0 24 24" fill="none"><path d="M20 12a8 8 0 1 1-3.2-6.4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M20 4v5h-5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>陪我聊聊天</button>' +
        '</div></div>';
      return;
    }
    var atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    box.innerHTML = WS.msgs.map(function (m) {
      var role = m.role || "user";
      var out = role === "assistant";
      var who = out ? "若" : (role === "tool" ? "工具" : "你");
      var ava = out
        ? '<span class="convAva">' + (window.RivuletAvatar ? window.RivuletAvatar.html({size: "md"}) : "") + "</span>"
        : '<span class="convAva convAvaUser">你</span>';
      return '<div class="convMsg ' + (out ? "out" : "in") + '">' + ava +
        '<div class="convBody">' +
        '<div class="ch"><b>' + who + "</b><span>" + esc(String(m.created_at || "").slice(5, 16)) + "</span></div>" +
        '<div class="cb">' + esc(out ? wsErrText(m.content || "") : (m.content || "")) + "</div></div></div>";
    }).join("");
    if (atBottom) box.scrollTop = box.scrollHeight;
  }
  function wsSend() {
    if (WS.sending) return;
    var inp = $("wsInput"), text = inp ? inp.value.trim() : "";
    if (!text) { toast("先说点什么"); return; }
    WS.sending = true;
    if ($("wsSend")) $("wsSend").disabled = true;
    var body = { text: text };
    if (WS.conv) body.conversation_id = WS.conv;
    WS.msgs.push({ role: "user", content: text, created_at: new Date().toISOString() });
    renderWsMsgs();
    if (inp) inp.value = "";
    svcPost("workspace", "/api/chat", body).then(function (r) {
      if (r && r.ok) {
        WS.conv = r.conversation_id || WS.conv;
        loadWsMessages(WS.conv);
        loadWsConvs();
        loadWsTasks();
        if (r.tool_calls && r.tool_calls.length) toast("用了工具:" + r.tool_calls.map(function (t) { return t.name; }).join("、"));
      } else toast((r && r.error) || "没回上");
    }).catch(function (e) { toast("对话失败:" + wsErrText(e.message || e)); })
      .then(function () { WS.sending = false; if ($("wsSend")) $("wsSend").disabled = false; });
  }
  function wsSaveModel() {
    var body = {
      name: "主模型",
      provider: ($("wsProvider") || {}).value || "deepseek",
      base_url: ($("wsBase") || {}).value.trim(),
      model: ($("wsModel") || {}).value.trim(),
      enabled: true
    };
    var key = ($("wsKey") || {}).value.trim();
    if (key) body.api_key = key;
    if (!body.model) { toast("模型名要填(比如 deepseek-chat)"); return; }
    svcPost("workspace", "/api/models", body).then(function (r) {
      if (r && r.ok) {
        var old = WS.models.filter(function (m) { return m.enabled; });
        var done = Promise.resolve();
        old.forEach(function (m) {
          done = done.then(function () {
            return call("svc_patch", { service: "workspace", path: "/api/models/" + encodeURIComponent(m.id),
              body: { enabled: false } }).catch(function () {});
          });
        });
        return done.then(function () { toast("模型已保存并启用"); if ($("wsKey")) $("wsKey").value = ""; loadWsModel(); });
      }
      toast((r && r.error) || "保存失败");
    }).catch(function (e) { toast("保存失败:" + (e.message || e)); });
  }
  function wsTestModel() {
    if ($("wsTestModel")) $("wsTestModel").disabled = true;
    svcPost("workspace", "/api/chat", { text: "用一句话自我介绍,别超过 20 字" }).then(function (r) {
      toast(r && r.ok ? ("模型回:" + String(r.reply || "").slice(0, 40)) : wsErrText((r && r.error) || "没回上"));
      if (r && r.ok) { loadWsConvs(); loadWsTasks(); }
    }).catch(function (e) { toast("测试失败:" + wsErrText(e.message || e)); })
      .then(function () { if ($("wsTestModel")) $("wsTestModel").disabled = false; });
  }
  function loadWsTasks() {
    svcGet("workspace", "/api/tasks").then(function (r) {
      WS.tasks = (r && r.items) || [];
      var box = $("wsTasks"), tag = $("wsTaskTag");
      if (tag) tag.textContent = WS.tasks.length ? WS.tasks.length + " 条" : "还没有";
      if (!box) return;
      box.innerHTML = WS.tasks.length
        ? WS.tasks.slice(0, 20).map(function (t) {
          return '<div class="auditRow"><span class="mono">' + esc(String(t.created_at || "").slice(5, 16)) + "</span>" +
            '<span class="' + (t.status === "done" ? "ok" : (t.status === "failed" ? "bad" : "warn")) + '">' + esc(t.status) + "</span>" +
            '<span class="txt">' + esc(t.title || "") + "</span></div>";
        }).join("")
        : '<p class="biliEmpty">还没有任务</p>';
    }).catch(function () {});
  }
  var WS_PRESETS = {
    openai: { provider: "openai", base: "https://api.openai.com/v1", model: "gpt-4o-mini",
              tip: "ChatGPT:去 platform.openai.com → API keys 建一把,地址一般不用改" },
    deepseek: { provider: "deepseek", base: "https://api.deepseek.com/v1", model: "deepseek-chat",
                tip: "DeepSeek:去 platform.deepseek.com → API keys" },
    relay: { provider: "custom", base: "https://你的中转站地址/v1", model: "按对方模型列表填",
             tip: "中转站:地址用对方给你的,通常以 /v1 结尾;模型名按对方列表抄" },
    ollama: { provider: "ollama", base: "http://127.0.0.1:11434/v1", model: "qwen2.5:7b",
              tip: "本地 Ollama:先在本机跑 ollama serve,Key 随便填或留空" },
    opencode: { provider: "custom", base: "https://你的opencode地址/v1", model: "按后台模型列表填",
                tip: "OpenCode Zen:用后台给的 API 地址(带 /v1)+ 模型名;只有订阅没有 API 的话接不了" }
  };
  function wsApplyPreset(key) {
    var p = WS_PRESETS[key];
    if (!p) return;
    if ($("wsProvider")) $("wsProvider").value = p.provider;
    if ($("wsBase")) $("wsBase").value = p.base;
    if ($("wsModel")) $("wsModel").value = p.model;
    toast(p.tip);
  }
  function wsErrText(msg) {
    var m = String(msg || "");
    if (/401|403|Unauthorized|invalid.*key|api.?key/i.test(m)) return "Key 不对或没权限 —— 去后台重新复制一把完整 Key";
    if (/404|not found|model.*not.*exist/i.test(m)) return "地址或模型名不对 —— 检查地址是否以 /v1 结尾、模型名是否抄对";
    if (/连不上|超时|timeout|connect|ECONN|ENOTFOUND/i.test(m)) return "连不上接口 —— 地址写错或网络不通";
    if (/429|rate limit|quota/i.test(m)) return "额度用完了或被限流 —— 去后台看余额";
    return m;
  }
  (function bindWorkspace() {
    var s = $("wsSend"); if (s) s.addEventListener("click", wsSend);
    var i = $("wsInput");
    if (i) {
      i.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); wsSend(); }
      });
      i.addEventListener("input", function () {
        this.style.height = "auto";
        this.style.height = Math.min(120, this.scrollHeight) + "px";
      });
    }
    var r = $("wsRefresh"); if (r) r.addEventListener("click", function () { enterWorkspace(); toast("已刷新"); });
    var sm = $("wsSaveModel"); if (sm) sm.addEventListener("click", wsSaveModel);
    var tm = $("wsTestModel"); if (tm) tm.addEventListener("click", wsTestModel);
    var pre = $("wsPresets");
    if (pre) pre.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-p]") : null;
      if (b) wsApplyPreset(b.getAttribute("data-p"));
    });
    // 新工作区控件
    function wsNewChat() {
      WS.conv = null; WS.msgs = [];
      renderWsMsgs(); renderWsPanel();
      var dr = $("wsDrawer"); if (dr) dr.hidden = true;
      var inp = $("wsInput"); if (inp) { inp.value = ""; inp.focus(); }
    }
    function wsToggleDrawer(force) {
      var dr = $("wsDrawer"); if (!dr) return;
      dr.hidden = (force === undefined) ? !dr.hidden : force;
      renderWsSide();
    }
    var home = $("wsHomeBtn"); if (home) home.addEventListener("click", function () { switchView("home"); });
    var hist = $("wsHistoryBtn"); if (hist) hist.addEventListener("click", function () { wsToggleDrawer(); });
    var nameBtn = $("wsConvNameBtn"); if (nameBtn) nameBtn.addEventListener("click", function () { wsToggleDrawer(); });
    var dclose = $("wsDrawerClose"); if (dclose) dclose.addEventListener("click", function () { wsToggleDrawer(false); });
    var nchat = $("wsNewChat"); if (nchat) nchat.addEventListener("click", wsNewChat);
    var qnew = $("wsQNew"); if (qnew) qnew.addEventListener("click", wsNewChat);
    var qfile = $("wsQFile"); if (qfile) qfile.addEventListener("click", function () {
      call("pick_file", { kind: "any" }).then(function (p) {
        if (p) { var inp = $("wsInput"); if (inp) { inp.value = "文件：" + p; inp.focus(); } }
      }).catch(function () {});
    });
    var cfg = $("wsCfgToggle"); if (cfg) cfg.addEventListener("click", function () {
      var panel = $("wsCfgPanel"); if (!panel) return;
      panel.hidden = !panel.hidden;
      this.classList.toggle("open", !panel.hidden);
    });
    var qmodel = $("wsQModel"); if (qmodel) qmodel.addEventListener("click", function () {
      var panel = $("wsCfgPanel"); if (panel) { panel.hidden = false; var t = $("wsCfgToggle"); if (t) t.classList.add("open"); }
      toast("在右侧填写并启用模型");
    });
    var chip = $("wsModelChip"); if (chip) chip.addEventListener("click", function () {
      var panel = $("wsCfgPanel"); if (panel) { panel.hidden = false; var t = $("wsCfgToggle"); if (t) t.classList.add("open"); }
    });
    var qlogs = $("wsQLogs"); if (qlogs) qlogs.addEventListener("click", function () { switchView("cmds"); });
    var tAttach = $("wsTAttach"); if (tAttach) tAttach.addEventListener("click", function () {
      call("pick_file", { kind: "any" }).then(function (p) { if (p) { var inp = $("wsInput"); if (inp) { inp.value = "文件：" + p; inp.focus(); } } }).catch(function () {});
    });
    var tImage = $("wsTImage"); if (tImage) tImage.addEventListener("click", function () {
      call("pick_file", { kind: "image" }).then(function (p) { if (p) { var inp = $("wsInput"); if (inp) { inp.value = "图片：" + p; inp.focus(); } } }).catch(function () {});
    });
    var tCode = $("wsTCode"); if (tCode) tCode.addEventListener("click", function () {
      var inp = $("wsInput"); if (!inp) return;
      inp.value = (inp.value ? inp.value + "\n" : "") + "```\n\n```";
      inp.focus(); inp.setSelectionRange(inp.value.length - 4, inp.value.length - 4);
    });
    var tWeb = $("wsTWeb"); if (tWeb) tWeb.addEventListener("click", function () { toast("联网检索会在对话里按需触发"); });
    var msgs = $("wsMsgs");
    if (msgs) msgs.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-suggest]") : null;
      if (!b) return;
      var inp = $("wsInput"); if (inp) { inp.value = b.getAttribute("data-suggest"); inp.focus(); }
    });
  })();

  // ================= 一键部署 =================
  var SETUP = { steps: [], timer: null, preset: "deepseek", bili: {}, modelSaved: false };

  var MODEL_PRESETS = [
    { g: "国内", k: "deepseek", name: "DeepSeek", provider: "deepseek", base: "https://api.deepseek.com/v1", model: "deepseek-chat", how: "platform.deepseek.com → API keys" },
    { g: "国内", k: "qwen", name: "通义千问(阿里百炼)", provider: "custom", base: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus", how: "bailian.console.aliyun.com → API-KEY" },
    { g: "国内", k: "kimi", name: "Kimi(月之暗面)", provider: "custom", base: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k", how: "platform.moonshot.cn → API Keys" },
    { g: "国内", k: "glm", name: "智谱 GLM", provider: "custom", base: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-plus", how: "open.bigmodel.cn → API Keys" },
    { g: "国内", k: "baichuan", name: "百川", provider: "custom", base: "https://api.baichuan-ai.com/v1", model: "Baichuan4", how: "platform.baichuan-ai.com → API Key" },
    { g: "国内", k: "doubao", name: "豆包(火山方舟)", provider: "custom", base: "https://ark.cn-beijing.volces.com/api/v3", model: "填你的推理接入点ID", how: "console.volcengine.com/ark → API Key + 接入点" },
    { g: "国内", k: "minimax", name: "MiniMax", provider: "custom", base: "https://api.minimax.chat/v1", model: "abab6.5s-chat", how: "platform.minimaxi.com → 接口密钥" },
    { g: "国内", k: "yi", name: "零一万物 Yi", provider: "custom", base: "https://api.lingyiwanwu.com/v1", model: "yi-large", how: "platform.lingyiwanwu.com → API Key" },
    { g: "国内", k: "hunyuan", name: "腾讯混元", provider: "custom", base: "https://api.hunyuan.cloud.tencent.com/v1", model: "hunyuan-turbo", how: "console.cloud.tencent.com/hunyuan" },
    { g: "国内", k: "spark", name: "讯飞星火", provider: "custom", base: "https://spark-api-open.xf-yun.com/v1", model: "generalv3.5", how: "console.xfyun.cn → 星火 API" },
    { g: "国内", k: "stepfun", name: "阶跃星辰", provider: "custom", base: "https://api.stepfun.com/v1", model: "step-2-16k", how: "platform.stepfun.com → API Key" },
    { g: "国内", k: "siliconflow", name: "硅基流动", provider: "custom", base: "https://api.siliconflow.cn/v1", model: "Qwen/Qwen2.5-72B-Instruct", how: "cloud.siliconflow.cn → API 密钥" },
    { g: "国外", k: "openai", name: "ChatGPT(OpenAI)", provider: "openai", base: "https://api.openai.com/v1", model: "gpt-4o-mini", how: "platform.openai.com → API keys" },
    { g: "国外", k: "claude", name: "Claude(Anthropic,需中转)", provider: "custom", base: "https://你的中转地址/v1", model: "claude-sonnet-4-5", how: "Anthropic 原生不是 OpenAI 协议,要用中转站" },
    { g: "国外", k: "gemini", name: "Google Gemini", provider: "custom", base: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-2.0-flash", how: "aistudio.google.com → API key" },
    { g: "国外", k: "grok", name: "Grok(xAI)", provider: "custom", base: "https://api.x.ai/v1", model: "grok-2-latest", how: "console.x.ai → API Keys" },
    { g: "国外", k: "mistral", name: "Mistral", provider: "custom", base: "https://api.mistral.ai/v1", model: "mistral-large-latest", how: "console.mistral.ai → API Keys" },
    { g: "国外", k: "openrouter", name: "OpenRouter", provider: "custom", base: "https://openrouter.ai/api/v1", model: "openai/gpt-4o", how: "openrouter.ai/keys" },
    { g: "国外", k: "groq", name: "Groq", provider: "custom", base: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile", how: "console.groq.com/keys" },
    { g: "国外", k: "together", name: "Together", provider: "custom", base: "https://api.together.xyz/v1", model: "meta-llama/Llama-3.3-70B-Instruct-Turbo", how: "api.together.xyz → API Keys" },
    { g: "本地", k: "ollama", name: "Ollama(本地)", provider: "ollama", base: "http://127.0.0.1:11434/v1", model: "qwen2.5:7b", how: "本机先 ollama serve,再 ollama pull 模型" },
    { g: "本地", k: "lmstudio", name: "LM Studio(本地)", provider: "custom", base: "http://127.0.0.1:1234/v1", model: "按 LM Studio 里加载的模型", how: "LM Studio 里开启本地服务" },
    { g: "本地", k: "vllm", name: "vLLM(本地)", provider: "custom", base: "http://127.0.0.1:8000/v1", model: "按你部署的模型", how: "vLLM 起 OpenAI 兼容服务" },
    { g: "其它", k: "relay", name: "中转站 / 公司网关", provider: "custom", base: "https://你的地址/v1", model: "按对方列表", how: "用对方给的接口地址(带 /v1)和模型名" },
    { g: "其它", k: "opencode", name: "OpenCode Zen", provider: "custom", base: "https://后台给的地址/v1", model: "后台列表里的模型名", how: "用 OpenCode 后台给的 API 地址;只有订阅没有 API 接不了" }
  ];

  function presetOf(k) {
    for (var i = 0; i < MODEL_PRESETS.length; i++) if (MODEL_PRESETS[i].k === k) return MODEL_PRESETS[i];
    return MODEL_PRESETS[0];
  }

  function enterSetup() {
    loadSetup();
    if (!SETUP.timer) {
      SETUP.timer = setInterval(function () {
        if (state.view !== "setup") return;
        // 正在部署表单里打字时别重绘,否则 3 秒一次会吞掉输入
        var a = document.activeElement;
        if (a && a.closest && a.closest("#setupSteps")) return;
        loadSetup(true);
      }, 3000);
    }
  }
  function loadSetup(quiet) {
    svcGet("setup", "/api/setup/status").then(function (r) {
      SETUP.steps = (r && r.steps) || [];
      SETUP.done = (r && r.done) || 0;
      SETUP.total = (r && r.total) || 3;
      renderSetup();
      renderSetupRail();
      onSetupProgress(r);   // 三步完成 → 自动结束引导
    }).catch(function (e) {
      if (!quiet) {
        var box = $("setupSteps");
        if (box) box.innerHTML = '<p class="biliEmpty">一键部署服务没起来:' + esc(e.message || e) + "</p>";
      }
    });
    svcGet("setup", "/api/setup/bili/status").then(function (r) { SETUP.bili = r || {}; }).catch(function () {});
  }
  function renderSetupRail() {
    var txt = $("lrSetupTxt"), dot = $("lrSetupDot"), chip = $("homeSetup");
    var done = SETUP.done || 0, total = SETUP.total || 3;
    if (txt) txt.textContent = done + "/" + total;
    if (dot) dot.className = "svcDot" + (done >= total ? " on" : (done > 0 ? " warn" : ""));
    if (chip) chip.textContent = "一键部署 " + done + "/" + total;
  }
  function renderSetup() {
    var box = $("setupSteps"); if (!box) return;
    var steps = SETUP.steps || [];
    var done = SETUP.done || 0, total = SETUP.total || 3;
    var bar = $("setupProgBar"); if (bar) bar.style.width = Math.round(done * 100 / (total || 1)) + "%";
    var pt = $("setupProgTxt"); if (pt) pt.textContent = done + " / " + total;
    var dot = $("setupDot");
    if (dot) dot.className = "svcDot big " + (done >= total ? "on" : (done > 0 ? "warn" : ""));
    var sub = $("setupSub");
    if (sub) sub.textContent = done >= total
      ? "三步都好了 —— 现在可以直接用"
      : ("还差 " + (total - done) + " 步:跟着下面走就行");
    if (!steps.length) { box.innerHTML = '<p class="biliEmpty">读取中…</p>'; return; }

    var html = steps.map(function (s, i) {
      var st = s.state || "todo";
      var badge = st === "done" ? "已完成" : (st === "waiting" ? "等你操作" : (st === "error" ? "出错了" : "待办"));
      var cls = st === "done" ? "ok" : (st === "waiting" ? "warn" : (st === "error" ? "bad" : ""));
      var h = '<div class="setupCard glass ' + (st === "done" ? "done" : "") + '">' +
        '<div class="scHead"><span class="scNo">' + (i + 1) + '</span>' +
        '<b>' + esc(s.name) + '</b>' +
        '<span class="scBadge ' + cls + '">' + badge + '</span></div>' +
        '<p class="scDetail">' + esc(s.detail || "") + '</p>';

      if (s.key === "qq") {
        if (s.qr) {
          h += '<div class="scQr"><img alt="QQ 二维码" src="data:image/png;base64,' + s.qr + '"></div>' +
            '<p class="scHint">用手机 QQ 扫码登录(扫完自动连上)</p>';
        }
        if (st !== "done") {
          h += '<div class="scActs"><button type="button" data-act="qq">' +
            (s.qr ? "重新出码" : "开始配置 QQ") + '</button></div>';
        }
      } else if (s.key === "bili") {
        var bq = s.qr || (SETUP.bili && SETUP.bili.qr_b64) || "";
        if (bq) {
          h += '<div class="scQr"><img alt="B站 二维码" src="data:image/png;base64,' + bq + '"></div>' +
            '<p class="scHint">用手机 B站 扫码登录(扫完自动写入登录态)</p>';
        }
        if (st !== "done") {
          h += '<div class="scActs"><button type="button" data-act="bili">' +
            (bq ? "重新生成二维码" : "开始配置 B站") + '</button></div>';
        }
      } else if (s.key === "model") {
        if (st === "done") {
          h += '<div class="scActs"><button type="button" class="ghost" data-act="model-toggle">换一个模型</button></div>';
        }
        h += '<div class="scForm' + (st === "done" && !SETUP.modelOpen ? " hidden" : "") + '" id="setupModelForm">' +
          '<label class="pField"><span>选服务商(共 ' + MODEL_PRESETS.length + ' 个)</span>' +
          '<select id="setupPreset">' + (function () {
            var out = "", lastG = "";
            MODEL_PRESETS.forEach(function (p) {
              if (p.g !== lastG) { if (lastG) out += "</optgroup>"; out += '<optgroup label="' + p.g + '">'; lastG = p.g; }
              out += '<option value="' + p.k + '">' + esc(p.name) + "</option>";
            });
            if (lastG) out += "</optgroup>";
            return out;
          })() + '</select></label>' +
          '<label class="pField"><span>接口地址</span><input id="setupBase" placeholder="选中服务商后自动填"></label>' +
          '<label class="pField"><span>模型名</span><input id="setupModelName" placeholder="例:gpt-4o-mini"></label>' +
          '<label class="pField"><span>API Key</span><input id="setupKey" type="password" placeholder="sk-..."></label>' +
          '<p class="scHint" id="setupHow">—</p>' +
          '<div class="scActs"><button type="button" data-act="model-save">保存并启用</button>' +
          '<button type="button" class="ghost" data-act="model-test">测试一句</button></div>' +
          '</div>';
      }
      return h + "</div>";
    }).join("");
    box.innerHTML = html;

    // 绑定
    var sel = $("setupPreset");
    if (sel) {
      sel.value = SETUP.preset;
      applySetupPreset(SETUP.preset);
      sel.addEventListener("change", function () { SETUP.preset = this.value; applySetupPreset(this.value); });
    }
    var acts = box.querySelectorAll("[data-act]");
    for (var i = 0; i < acts.length; i++) {
      acts[i].addEventListener("click", function () {
        var a = this.getAttribute("data-act");
        if (a === "qq") setupQq();
        else if (a === "bili") setupBili();
        else if (a === "model-save") setupModelSave(this);
        else if (a === "model-test") setupModelTest(this);
        else if (a === "model-toggle") {
          SETUP.modelOpen = !SETUP.modelOpen;
          var f = $("setupModelForm");
          if (f) f.classList.toggle("hidden", !SETUP.modelOpen);
        }
      });
    }
  }
  function applySetupPreset(k) {
    var p = presetOf(k);
    if ($("setupBase")) $("setupBase").value = p.base;
    if ($("setupModelName")) $("setupModelName").value = p.model;
    if ($("setupHow")) $("setupHow").textContent = "去哪拿 Key:" + p.how;
  }
  function setupQq() {
    toast("正在后台启动 QQ 协议端…");
    svcPost("setup", "/api/setup/qq/start", {}).then(function (r) {
      if (r && r.ok) { toast(r.already ? "协议端已经在跑了" : (r.hint || "已启动,等二维码")); }
      else toast((r && r.error) || "启动失败");
      setTimeout(function () { loadSetup(); }, 2500);
    }).catch(function (e) { toast("启动失败:" + (e.message || e)); });
  }
  function setupBili() {
    toast("正在生成 B站 登录二维码…");
    svcPost("setup", "/api/setup/bili/gen", {}).then(function () {
      setTimeout(function () { loadSetup(); }, 2000);
    }).catch(function (e) { toast("生成失败:" + (e.message || e)); });
  }
  function setupModelSave(btn) {
    var p = presetOf(SETUP.preset);
    var body = {
      provider: p.provider,
      base_url: ($("setupBase") || {}).value.trim(),
      model: ($("setupModelName") || {}).value.trim(),
      api_key: ($("setupKey") || {}).value.trim()
    };
    if (!body.model) { toast("模型名要填"); return; }
    btn.disabled = true;
    svcPost("setup", "/api/setup/model", body).then(function (r) {
      if (r && r.ok) {
        toast("模型已保存并启用,正在测一句…");
        if ($("setupKey")) $("setupKey").value = "";
        SETUP.modelOpen = false;
        return svcPost("setup", "/api/setup/model/test", {}).then(function (t) {
          toast(t && t.ok ? ("通了!她说:" + String(t.reply || "").slice(0, 30)) : ("保存了,但测试没通:" + wsErrText((t && t.error) || "")));
          loadSetup();
        });
      }
      toast((r && r.error) || "保存失败");
    }).catch(function (e) { toast("保存失败:" + (e.message || e)); })
      .then(function () { btn.disabled = false; });
  }
  function setupModelTest(btn) {
    btn.disabled = true;
    svcPost("setup", "/api/setup/model/test", {}).then(function (t) {
      toast(t && t.ok ? ("模型回:" + String(t.reply || "").slice(0, 40)) : ("测试没通:" + wsErrText((t && t.error) || "")));
    }).catch(function (e) { toast("测试失败:" + wsErrText(e.message || e)); })
      .then(function () { btn.disabled = false; });
  }
  (function bindSetup() {
    var r = $("setupRefresh");
    if (r) r.addEventListener("click", function () { loadSetup(); toast("已刷新"); });
    var chip = $("homeSetup");
    if (chip) chip.addEventListener("click", function () { switchView("setup"); });
  })();

  // ---------------- v4 新壳的补丁绑定 ----------------
  (function bindV4() {
    // 左栏迷你鱼缸 -> 小窝
    var tank = $("tankCard");
    if (tank) {
      tank.addEventListener("click", function () { switchView("fish"); });
      tank.setAttribute("role", "button");
      tank.setAttribute("tabindex", "0");
      tank.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); switchView("fish"); } });
    }
    // 工作区三个快捷入口:填进输入框直接发
    var quick = $("wsQuick");
    if (quick) {
      quick.addEventListener("click", function (e) {
        var b = e.target.closest ? e.target.closest("button") : null;
        if (!b) return;
        var inp = $("wsInput");
        if (!inp) return;
        inp.value = b.textContent.trim();
        inp.focus();
        if (typeof wsSend === "function") wsSend();
      });
    }
    // 概览模块详情卡:/cmds 之类的锚点
    document.addEventListener("click", function (e) {
      var a = e.target.closest ? e.target.closest("[data-jump]") : null;
      if (!a) return;
      e.preventDefault();
      switchView(a.getAttribute("data-jump"));
    });
    // 点左上角品牌枕回概览
    var brand = document.querySelector(".brandBar");
    if (brand) brand.addEventListener("click", function () { switchView("home"); });
  })();

  // ---------------- 启动 ----------------
  (function boot() {
    call("get_api_base", {}).then(function (b) {
      if (b) API_BASE = b;
      $("apiLine").textContent = "服务器 " + API_BASE;
      $("regApiLine").textContent = API_BASE;
    }).catch(function () {});

    call("app_info", {}).then(function (i) { if (i && i.version) $("ver").textContent = i.version; }).catch(function () {});

    call("restore", {}).then(function (s) {
      if (s && s.user) { state.user = s.user; showApp(); } else { showLogin(); }
    }).catch(function () { showLogin(); });
  })();
})();
