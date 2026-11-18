/* Rivulet 若 · 概念版首页交互层 v5
 * 职责:右栏「模型状态 / 运行状态」卡、Ctrl+K 搜索面板、底部 AI 输入框、
 *       首页卡片跳转、全局快捷键、返回设置 chip。
 * 依赖 app.js 的 window.RuoApp($/state/call/svcGet/svcPost/switchView/toast)
 * 与 ruo:data / ruo:view 事件;不直接碰后端、不造任何假数据。
 * 原则:所有 DOM 访问判空,所有异步都 catch,任何情况下不抛错、不刷控制台。
 */
(function () {
  "use strict";

  // ================= 0. 基础工具 =================
  var $ = function (id) { return document.getElementById(id); };
  function app() { return window.RuoApp || null; }
  function noApp() { return Promise.reject(new Error("RuoApp 未就绪")); }
  function call(cmd, args) { var a = app(); return (a && a.call) ? a.call(cmd, args || {}) : noApp(); }
  function svcGet(service, path) { var a = app(); return (a && a.svcGet) ? a.svcGet(service, path) : noApp(); }
  function svcPost(service, path, body) { var a = app(); return (a && a.svcPost) ? a.svcPost(service, path, body || {}) : noApp(); }
  function switchView(v) { var a = app(); if (a && a.switchView) { try { a.switchView(v); } catch (e) {} } }
  function toast(msg) { var a = app(); if (a && a.toast) { try { a.toast(msg); } catch (e) {} } }
  function msgOf(e) { return (e == null) ? "未知错误" : String(e.message || e); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // 自己实现 closest,避免老 WebView 上 Element.closest 缺失
  function closest(el, sel) {
    while (el && el !== document) { if (el.matches && el.matches(sel)) return el; el = el.parentNode; }
    return null;
  }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  // 后端时间两种格式:SQLite 的 "YYYY-MM-DD HH:MM:SS"(本地时间)与 ISO
  function parseTs(s) {
    if (!s) return null;
    var str = String(s).trim();
    if (!str) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(str);
    if (m && !/[zZ]$|[+-]\d{2}:?\d{2}$/.test(str)) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
    var d = new Date(str.replace(" ", "T"));
    return isNaN(d.getTime()) ? null : d;
  }
  function dayKey(d) { return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()); }
  function appReady() { var v = $("appView"); return !!(v && !v.hidden); }

  // ================= 1. 右栏:模型状态 =================
  var lastStatus = null;   // 最近一次 /api/models/status
  var lastModels = [];     // 最近一次 /api/models 的 items
  // 卡片右上角「在线 / 未启用」;CSS 没给 .st.off 配色,未启用时用内联灰
  function setStLabel(el, on) {
    if (!el) return;
    el.className = "st " + (on ? "ok" : "off");
    el.innerHTML = "<i></i>" + (on ? "在线" : "未启用");
    var dot = el.querySelector("i");
    el.style.color = on ? "" : "var(--muted)";
    if (dot) { dot.style.background = on ? "" : "var(--muted)"; dot.style.boxShadow = on ? "" : "none"; }
  }
  function setDot(dot, on) {
    if (!dot) return;
    dot.style.background = on ? "" : "var(--muted)";
    dot.style.boxShadow = on ? "" : "none";
  }
  // 把 /api/models/status(或从模型列表推导出的同形状对象)铺到卡片 + chip
  function applyModelStatus(st, items) {
    st = st || {};
    var on = !!st.enabled;
    var modelName = st.model || st.name || "", provider = st.provider || "", base = st.base_url || "";
    if (!modelName && items && items.length) {
      for (var i = 0; i < items.length; i++) {
        if (items[i].enabled) {
          modelName = items[i].model || items[i].name || "";
          provider = items[i].provider || provider;
          base = items[i].base_url || base;
          break;
        }
      }
    }
    var nameEl = $("homeModelName");
    if (nameEl) nameEl.textContent = on ? (modelName || "已启用模型") : "未启用模型";
    var descEl = $("homeModelDesc");
    if (descEl) descEl.textContent = on ? ("已启用 · " + (provider || "自定义")) : "在工作区启用一个模型";
    var card = nameEl ? closest(nameEl, ".modelCard") : null; // 卡片状态没有 id,按容器定位
    setStLabel(card ? card.querySelector(".scHead .st") : null, on);
    var chipName = $("cModelName");
    if (chipName) chipName.textContent = on ? (modelName || "已启用模型") : "未启用模型";
    var chip = $("cModel");
    setDot(chip ? chip.querySelector(".mcDot") : null, on);
    if (chip) chip.setAttribute("title", on
      ? ("当前模型:" + (modelName || provider || "已启用") + (base ? " · " + base : "")) : "点这里切换模型");
  }
  function refreshModelCard() {
    if (!app()) return;
    var cached = (app().state || {}).ws;
    if (cached) { lastStatus = cached; applyModelStatus(cached, lastModels); }
    Promise.all([
      svcGet("workspace", "/api/models/status").catch(function (e) { return { __err: msgOf(e) }; }),
      svcGet("workspace", "/api/models").catch(function (e) { return { __err: msgOf(e) }; })
    ]).then(function (r) {
      var st = (r[0] && !r[0].__err) ? r[0] : null;
      var items = (r[1] && r[1].items) || [];
      if (items.length) lastModels = items;
      if (!st) { // 状态接口挂了:从列表推导;列表也读不到就保持现状,不写假数据
        var cur = null;
        for (var i = 0; i < items.length; i++) { if (items[i].enabled) { cur = items[i]; break; } }
        if (!cur) { if (!cached) applyModelStatus({ enabled: false }, items); return; }
        st = { enabled: true, model: cur.model, name: cur.name, provider: cur.provider, base_url: cur.base_url, has_key: cur.has_key };
      }
      lastStatus = st;
      applyModelStatus(st, items);
    }).catch(function () { /* 服务没起来:保持现状 */ });
  }

  // ================= 2. 右栏:运行状态 + 7 天 sparkline =================
  function sparkValues(cmds) {
    var keys = [], map = {}, i, now = new Date();
    for (i = 6; i >= 0; i--) {
      var k = dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - i));
      keys.push(k); map[k] = 0;
    }
    for (i = 0; i < (cmds || []).length; i++) {
      var t = parseTs(cmds[i] && cmds[i].created_at);
      if (!t) continue;
      var kk = dayKey(t);
      if (Object.prototype.hasOwnProperty.call(map, kk)) map[kk]++;
    }
    return keys.map(function (k) { return map[k]; });
  }
  // 真实 7 天命令数 → 面积 + 折线 + 末点;没数据就只画一条细基线(area 留空)
  function renderSpark(vals) {
    var svg = $("homeSpark");
    if (!svg) return;
    vals = vals && vals.length ? vals : [0, 0, 0, 0, 0, 0, 0];
    var W = 240, H = 64, padX = 6, top = 10, bottom = 54, max = 0, i;
    for (i = 0; i < vals.length; i++) if (vals[i] > max) max = vals[i];
    var empty = max === 0;
    var n = vals.length, pts = [];
    for (i = 0; i < n; i++) {
      var x = (n === 1) ? (W / 2) : (padX + (W - 2 * padX) * i / (n - 1));
      var y = empty ? 52 : (bottom - (bottom - top) * (vals[i] / max));
      pts.push([x, y]);
    }
    var line = pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" ");
    var area = empty ? "" : ("M" + pts.map(function (p) { return p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" L") +
      " L" + pts[n - 1][0].toFixed(1) + " " + H + " L" + pts[0][0].toFixed(1) + " " + H + " Z");
    var last = pts[n - 1];
    svg.setAttribute("data-empty", empty ? "1" : "0");
    svg.innerHTML =
      '<defs><linearGradient id="sparkGrad" x1="0" y1="0" x2="1" y2="0">' +
      '<stop offset="0%" stop-color="#8E7BFF"/><stop offset="100%" stop-color="#6D8BFF"/></linearGradient>' +
      '<linearGradient id="sparkArea" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="rgba(109,139,255,.34)"/><stop offset="100%" stop-color="rgba(109,139,255,0)"/>' +
      "</linearGradient></defs>" +
      '<path class="area" d="' + area + '"/><polyline points="' + line + '"/>' +
      (empty ? "" : '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="2.6"/>');
  }
  // 只有「核心模块」离线才标红;hub(消息中心)/home(鱼的小窝)等外围缺失、
  // 以及节点没上线,都保持绿字「运行正常」,缺什么写进 meta,不夸大故障。
  var CORE_MODULES = { core: 1, memory: 1, workspace: 1 };
  function applyRunCard(ov, cmds, nodes) {
    ov = ov || {}; cmds = cmds || []; nodes = nodes || [];
    var modules = ov.modules || [], missingCore = [], missingPeriph = [], i;
    for (i = 0; i < modules.length; i++) {
      if (modules[i].ok) continue;
      var nm = modules[i].label || modules[i].name || "未知模块";
      if (CORE_MODULES[modules[i].name]) missingCore.push(nm);
      else missingPeriph.push(nm); // 未登记的新模块也按外围算,避免误报红
    }
    var nodesOnline = 0;
    for (i = 0; i < nodes.length; i++) if (nodes[i].online) nodesOnline++;
    var bad = missingCore.length > 0;
    var stateEl = $("homeRunState");
    if (stateEl) {
      stateEl.className = "runState" + (bad ? " bad" : "");
      stateEl.innerHTML = "<i></i>" + (bad ? "部分服务离线" : "运行正常");
    }
    var meta = "节点 " + nodesOnline + "/" + nodes.length + " · 命令 " + cmds.length;
    var missing = missingCore.concat(missingPeriph);
    if (missing.length) meta += " · 缺 " + missing.join("、");
    var metaEl = $("homeRunMeta");
    if (metaEl) metaEl.textContent = meta;
    renderSpark(sparkValues(cmds));
  }
  function refreshRunCard() {
    if (!app()) return;
    var st = app().state || {};
    Promise.all([
      call("overview", {}).catch(function (e) { return { __err: msgOf(e) }; }),
      call("cmds_list", { limit: 200 }).catch(function (e) { return { __err: msgOf(e) }; }),
      call("nodes_list", {}).catch(function (e) { return { __err: msgOf(e) }; })
    ]).then(function (r) {
      var ov = (r[0] && !r[0].__err) ? r[0] : (st.overview || null);
      var cmds = (r[1] && r[1].commands) ? r[1].commands : (st.cmds || []);
      var nodes = (r[2] && r[2].nodes) ? r[2].nodes : (st.nodes || []);
      applyRunCard(ov, cmds, nodes);
    }).catch(function () { /* 服务没起来:保留上一版 */ });
  }

  // ================= 3. 搜索面板(Ctrl+K) =================
  // 小图标:统一 24 viewBox + currentColor 描边,颜色由 .pRow .pi 决定
  var ICON = {
    home: '<svg viewBox="0 0 24 24" fill="none"><path d="M4 10.6 12 4l8 6.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M6.4 9.8V20h11.2V9.8" stroke="currentColor" stroke-width="1.7"/></svg>',
    workspace: '<svg viewBox="0 0 24 24" fill="none"><rect x="3.6" y="4.6" width="16.8" height="11.6" rx="2.4" stroke="currentColor" stroke-width="1.7"/><path d="M8.6 20h6.8M12 16.6V20" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M8.2 9.2l2.5 2.3-2.5 2.3M13.2 13.8h3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    qq: '<svg viewBox="0 0 24 24" fill="none"><path d="M12 3.6c3.2 0 5.2 2.4 5.2 5.6v1.4l2.2 3c.5.7 0 1.5-.8 1.5h-1.6c-.5 2.2-2.5 4-5 4s-4.5-1.8-5-4H5.4c-.8 0-1.3-.8-.8-1.5l2.2-3V9.2c0-3.2 2-5.6 5.2-5.6Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><circle cx="9.9" cy="9.6" r=".9" fill="currentColor"/><circle cx="14.1" cy="9.6" r=".9" fill="currentColor"/></svg>',
    bili: '<svg viewBox="0 0 24 24" fill="none"><rect x="3.4" y="6.8" width="17.2" height="12.2" rx="3" stroke="currentColor" stroke-width="1.7"/><path d="M8 3.4l2.4 2.4M16 3.4l-2.4 2.4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M10.2 11v3.6l3.4-1.8-3.4-1.8Z" fill="currentColor"/></svg>',
    fish: '<svg viewBox="0 0 24 24" fill="none"><path d="M3.2 12c3-4.4 5.8-6.6 8.4-6.6s5 2.2 7.8 6.6c-2.8 4.4-5.2 6.6-7.8 6.6S6.2 16.4 3.2 12Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><circle cx="14.8" cy="10.6" r="1.1" fill="currentColor"/></svg>',
    settings: '<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.7"/><path d="M19.2 13.3a7.4 7.4 0 0 0 0-2.6l2-1.6-2-3.4-2.3.9a7.2 7.2 0 0 0-2.3-1.3L14.2 2.6h-4.4l-.4 2.7c-.8.3-1.6.8-2.3 1.3l-2.3-.9-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.6l-2 1.6 2 3.4 2.3-.9c.7.6 1.5 1 2.3 1.3l.4 2.7h4.4l.4-2.7c.8-.3 1.6-.7 2.3-1.3l2.3.9 2-3.4-2-1.6Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
    memory: '<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="5.6" r="2.1" stroke="currentColor" stroke-width="1.6"/><circle cx="6.4" cy="15.2" r="2.1" stroke="currentColor" stroke-width="1.6"/><circle cx="17.6" cy="15.2" r="2.1" stroke="currentColor" stroke-width="1.6"/><path d="M12 7.7v3.6M6.4 13.1v-.9h11.2v.9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    mood: '<svg viewBox="0 0 24 24" fill="none"><path d="M12 20s-7.4-4.4-7.4-9.2A4.2 4.2 0 0 1 12 7.6a4.2 4.2 0 0 1 7.4 3.2C19.4 15.6 12 20 12 20Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
    persona: '<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="8" stroke="currentColor" stroke-width="1.7"/><path d="M8.8 10.2h.01M15.2 10.2h.01M8.8 14.2c.9 1.4 2 2 3.2 2s2.3-.6 3.2-2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    account: '<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="8.2" r="3.4" stroke="currentColor" stroke-width="1.7"/><path d="M5.4 20c.7-3.4 3.4-5.2 6.6-5.2s5.9 1.8 6.6 5.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
    nodes: '<svg viewBox="0 0 24 24" fill="none"><rect x="3.6" y="4.4" width="16.8" height="6" rx="2" stroke="currentColor" stroke-width="1.6"/><rect x="3.6" y="13.6" width="16.8" height="6" rx="2" stroke="currentColor" stroke-width="1.6"/><circle cx="7.4" cy="7.4" r="1" fill="currentColor"/><circle cx="7.4" cy="16.6" r="1" fill="currentColor"/></svg>',
    cmds: '<svg viewBox="0 0 24 24" fill="none"><rect x="3.4" y="4.6" width="17.2" height="14.8" rx="2.6" stroke="currentColor" stroke-width="1.6"/><path d="M7.2 9.4l2.4 2.4-2.4 2.4M12.6 14.6h4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    setup: '<svg viewBox="0 0 24 24" fill="none"><path d="M13.8 3.4 5.6 13.2h4.8l-.6 7.4 8.6-9.8h-5l.4-7.4Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
    chat: '<svg viewBox="0 0 24 24" fill="none"><path d="M4.8 5.4h14.4v9.8H12l-4.4 3.4v-3.4H4.8V5.4Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M8.6 9.6h6.8M8.6 12.2h4.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    swap: '<svg viewBox="0 0 24 24" fill="none"><path d="M5 8.6h12.6M5 8.6l2.6-2.6M5 8.6l2.6 2.6M19 15.4H6.4M19 15.4l-2.6-2.6M19 15.4l-2.6 2.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none"><path d="M19.6 11a7.8 7.8 0 1 0-2.2 5.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M19.6 4.8V11h-6.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    logs: '<svg viewBox="0 0 24 24" fill="none"><path d="M5 6.6h14M5 12h14M5 17.4h9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>'
  };
  var NAV_ITEMS = [
    { kind: "nav", view: "home", icon: "home", title: "首页", hint: "概览" },
    { kind: "nav", view: "workspace", icon: "workspace", title: "工作区", hint: "对话 / 模型" },
    { kind: "nav", view: "qq", icon: "qq", title: "QQ Agent", hint: "消息 / 白名单" },
    { kind: "nav", view: "bili", icon: "bili", title: "B站 Agent", hint: "投稿 / 评论" },
    { kind: "nav", view: "fish", icon: "fish", title: "鱼的小窝", hint: "像素小家" },
    { kind: "nav", view: "settings", icon: "settings", title: "设置", hint: "功能中枢" },
    { kind: "nav", view: "memory", icon: "memory", title: "记忆树", hint: "统一记忆库" },
    { kind: "nav", view: "mood", icon: "mood", title: "心情", hint: "情绪 / 精力" },
    { kind: "nav", view: "persona", icon: "persona", title: "人格", hint: "说话的样子" },
    { kind: "nav", view: "account", icon: "account", title: "账号", hint: "身份 / 安全" },
    { kind: "nav", view: "nodes", icon: "nodes", title: "节点", hint: "Node Agent" },
    { kind: "nav", view: "cmds", icon: "cmds", title: "命令", hint: "下发记录" },
    { kind: "nav", view: "setup", icon: "setup", title: "一键部署", hint: "扫码 / 模型" }
  ];
  var ACT_ITEMS = [
    { kind: "act", act: "newchat", icon: "chat", title: "新建对话", hint: "Ctrl + N" },
    { kind: "act", act: "switchmodel", icon: "swap", title: "切换模型", hint: "Ctrl + M" },
    { kind: "act", act: "refresh", icon: "refresh", title: "刷新数据", hint: "重新读取" },
    { kind: "act", act: "logs", icon: "logs", title: "查看日志", hint: "Ctrl + L" }
  ];
  var paletteOpen = false, selIdx = 0, paletteQuery = "", memItems = [], memTimer = 0;
  function matchItem(it, q) { return !q || (String(it.title || "") + " " + String(it.hint || "")).toLowerCase().indexOf(q) >= 0; }
  function currentItems() {
    var q = paletteQuery.trim().toLowerCase(), out = [], i;
    for (i = 0; i < NAV_ITEMS.length; i++) if (matchItem(NAV_ITEMS[i], q)) out.push(NAV_ITEMS[i]);
    for (i = 0; i < ACT_ITEMS.length; i++) if (matchItem(ACT_ITEMS[i], q)) out.push(ACT_ITEMS[i]);
    if (q.length >= 1) out = out.concat(memItems);
    return out;
  }
  function renderPalette() {
    var list = $("paletteList");
    if (!list) return;
    var items = currentItems();
    if (selIdx >= items.length) selIdx = items.length ? items.length - 1 : 0;
    if (selIdx < 0) selIdx = 0;
    if (!items.length) {
      list.innerHTML = '<div class="pRow" style="color:var(--muted)"><span class="pi">' + ICON.home + "</span><span>没有匹配的结果</span></div>";
      return;
    }
    list.innerHTML = items.map(function (it, i) {
      return '<button type="button" class="pRow' + (i === selIdx ? " sel" : "") + '" data-idx="' + i + '">' +
        '<span class="pi">' + (ICON[it.icon] || ICON.home) + "</span><span>" + esc(it.title) + "</span>" +
        (it.hint ? "<small>" + esc(it.hint) + "</small>" : "") + "</button>";
    }).join("");
  }
  function moveSel(delta) {
    var items = currentItems();
    if (!items.length) return;
    selIdx = (selIdx + delta + items.length) % items.length;
    var list = $("paletteList");
    if (!list) return;
    var rows = list.querySelectorAll(".pRow");
    for (var i = 0; i < rows.length; i++) rows[i].classList.toggle("sel", i === selIdx);
    if (rows[selIdx] && rows[selIdx].scrollIntoView) { try { rows[selIdx].scrollIntoView({ block: "nearest" }); } catch (e) {} }
  }
  // 记忆搜索:250ms 防抖,结果追加到功能项后面
  function searchMemory(q) {
    if (memTimer) { clearTimeout(memTimer); memTimer = 0; }
    memTimer = setTimeout(function () {
      memTimer = 0;
      if (!app() || !app().call) return;
      call("memory_search", { q: q, limit: 6 }).then(function (r) {
        if (!paletteOpen || paletteQuery.trim() !== q) return; // 已经改词/关掉了
        memItems = ((r && r.items) || []).map(function (it) {
          var content = String((it && it.content) || "").replace(/\s+/g, " ").trim();
          var date = String((it && (it.occurred_at || it.created_at)) || "").slice(0, 10);
          return {
            kind: "mem", icon: "memory",
            title: content.length > 44 ? content.slice(0, 44) + "…" : (content || "(空记忆)"),
            hint: (it && it.source ? it.source : "记忆") + (date ? " · " + date : ""),
            content: content
          };
        });
        renderPalette();
      }).catch(function () { /* 记忆服务没起来:只显示功能项,不打扰 */ });
    }, 250);
  }
  function openPalette(value, keepFocus) {
    if (!appReady()) return;
    var el = $("palette");
    if (!el) return;
    closeModelDrop(); closeToolPop();
    paletteQuery = value || "";
    memItems = []; selIdx = 0;
    if (memTimer) { clearTimeout(memTimer); memTimer = 0; }
    var pi = $("paletteInput"), gs = $("globalSearch");
    if (pi) pi.value = paletteQuery;
    if (gs) gs.value = paletteQuery;
    el.hidden = false;
    paletteOpen = true;
    renderPalette();
    if (paletteQuery.trim().length >= 1) searchMemory(paletteQuery.trim());
    if (!keepFocus && pi) { try { pi.focus(); } catch (e) {} }
  }
  function closePalette() {
    var el = $("palette");
    if (el) el.hidden = true;
    paletteOpen = false;
    if (memTimer) { clearTimeout(memTimer); memTimer = 0; }
  }
  // 顶部搜索框与面板输入框双向同步,谁打字都算
  function onSearchInput(src) {
    if (!src) return;
    var v = src.value;
    var other = (src.id === "paletteInput") ? $("globalSearch") : $("paletteInput");
    if (other && other.value !== v) other.value = v;
    paletteQuery = v;
    memItems = []; selIdx = 0;
    var el = $("palette");
    if (el && el.hidden) { openPalette(v, true); return; }
    renderPalette();
    var q = v.trim();
    if (q.length >= 1) searchMemory(q);
    else if (memTimer) { clearTimeout(memTimer); memTimer = 0; }
  }
  function runPaletteAct(act) {
    if (act === "newchat") { switchView("workspace"); focusWsInput(); }
    else if (act === "switchmodel") { openModelDrop(); }
    else if (act === "refresh") {
      var b = $("btnRefresh");
      if (b) { try { b.click(); } catch (e) {} } else { refreshAll(); }
      toast("正在刷新…");
    }
    else if (act === "logs") { switchView("cmds"); }
  }
  function executeItem(it) {
    if (!it) return;
    closePalette();
    if (it.kind === "nav") { switchView(it.view); return; }
    if (it.kind === "act") { runPaletteAct(it.act); return; }
    if (it.kind === "mem") { switchView("memory"); toast(it.content ? ("记忆:" + it.content.slice(0, 80)) : "已打开记忆树"); }
  }

  // ================= 4. 底部 AI 输入框 =================
  var hintTimer = 0, sending = false;
  function showHint(msg, autoHide) {
    var h = $("homeHint");
    if (!h) return;
    h.hidden = false;
    h.textContent = msg;
    if (hintTimer) { clearTimeout(hintTimer); hintTimer = 0; }
    if (autoHide) hintTimer = setTimeout(function () { var x = $("homeHint"); if (x) x.hidden = true; hintTimer = 0; }, 4500);
  }
  function hideHint() {
    var h = $("homeHint");
    if (h) h.hidden = true;
    if (hintTimer) { clearTimeout(hintTimer); hintTimer = 0; }
  }
  function autosize() {
    var ta = $("homeAsk");
    if (!ta) return;
    ta.style.height = "auto";
    var h = ta.scrollHeight;
    if (h > 140) h = 140;
    if (h < 44) h = 44;
    ta.style.height = h + "px";
  }
  function ensureStatus() {
    if (lastStatus) return Promise.resolve(lastStatus);
    return svcGet("workspace", "/api/models/status").then(function (st) {
      lastStatus = st || { enabled: false };
      applyModelStatus(lastStatus, lastModels);
      return lastStatus;
    });
  }
  // 发送:先确认真的有启用模型,没启用就只给提示、绝不伪造回复
  function sendHome() {
    if (sending) return;
    var ta = $("homeAsk");
    if (!ta) return;
    var text = ta.value.trim();
    if (!text) { toast("先写点什么"); return; }
    sending = true;
    var btn = $("cSend");
    ensureStatus().then(function (st) {
      if (!st || !st.enabled) {
        showHint("还没有启用模型：去工作区 → 模型，填一个 API Key", false);
        sending = false;
        return;
      }
      if (btn) btn.disabled = true;
      showHint("若正在思考…", false);
      return svcPost("workspace", "/api/chat", { text: text }).then(function (r) {
        if (r && r.ok === false) { showHint("发送失败：" + (r.error || "没回上"), false); return; }
        ta.value = ""; autosize(); hideHint();
        switchView("workspace");
        toast("已发送，去工作区看回复");
      }).catch(function (e) {
        showHint("发送失败：" + msgOf(e), false);
      }).then(function () {
        if (btn) btn.disabled = false;
        sending = false;
      });
    }).catch(function (e) {
      showHint("读不到模型状态：" + msgOf(e), false);
      sending = false;
    });
  }
  // 在光标处插入;caretBack 把光标落到插入内容中间(代码块/引号)
  function insertAtCaret(text, caretBack) {
    var ta = $("homeAsk");
    if (!ta) return;
    var start = (ta.selectionStart == null) ? ta.value.length : ta.selectionStart;
    var end = (ta.selectionEnd == null) ? start : ta.selectionEnd;
    ta.value = ta.value.slice(0, start) + text + ta.value.slice(end);
    var pos = start + text.length - (caretBack || 0);
    if (pos < start) pos = start;
    try { ta.focus(); ta.setSelectionRange(pos, pos); } catch (e) {}
    autosize();
  }
  function pickFile(kind, label, insert) {
    call("pick_file", { kind: kind || "any" }).then(function (p) {
      if (!p) return; // 用户取消
      var name = String(p).split(/[\\/]/).pop();
      if (insert === false) { toast("已选择「" + name + "」"); return; }
      insertAtCaret("【" + (label || "文件") + "】" + name + " ");
      showHint("已加入「" + name + "」，可以直接发送或补充说明", true);
    }).catch(function (e) { showHint("选文件失败：" + msgOf(e), true); });
  }
  function insertCode() {
    insertAtCaret("\n```\n\n```\n", 4); // 光标落在代码块中间
    showHint("代码块已插入，把代码贴进去就行", true);
  }

  // ================= 5. 模型下拉(#cModel) =================
  var dropEl = null;
  function closeModelDrop() {
    if (dropEl && dropEl.parentNode) dropEl.parentNode.removeChild(dropEl);
    dropEl = null;
  }
  function openModelDrop() {
    if (dropEl) { closeModelDrop(); return; }
    if (!appReady()) return;
    svcGet("workspace", "/api/models").then(function (r) {
      var items = (r && r.items) || [];
      if (!items.length) { toast("还没有模型"); return; }
      lastModels = items;
      buildModelDrop(items);
    }).catch(function (e) { toast("读不到模型列表：" + msgOf(e)); });
  }
  function buildModelDrop(items) {
    var anchor = $("cModel");
    if (!anchor) return;
    closeModelDrop();
    var drop = document.createElement("div");
    drop.className = "cmDrop";
    items.forEach(function (m) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "cmItem" + (m.enabled ? " on" : "");
      var t = document.createElement("span");
      t.textContent = m.model || m.name || "(未命名模型)";
      b.appendChild(t);
      if (m.provider) {
        var s = document.createElement("small");
        s.textContent = m.provider;
        b.appendChild(s);
      }
      b.addEventListener("click", function () { chooseModel(m); });
      drop.appendChild(b);
    });
    document.body.appendChild(drop);
    // 按 chip 位置算 fixed 坐标,放不下就翻到上方/贴边
    var r = anchor.getBoundingClientRect();
    var w = drop.offsetWidth, h = drop.offsetHeight;
    var left = r.left, top = r.top - h - 8;
    if (left + w > window.innerWidth - 12) left = window.innerWidth - w - 12;
    if (left < 12) left = 12;
    if (top < 12) top = r.bottom + 8;
    drop.style.left = Math.round(left) + "px";
    drop.style.top = Math.round(top) + "px";
    dropEl = drop;
  }
  // 工作区只认第一个启用的模型:先关掉其它,再启用选中的
  function chooseModel(m) {
    closeModelDrop();
    if (!m || !m.id) return;
    var label = m.model || m.name || "这个模型";
    var chain = Promise.resolve();
    lastModels.forEach(function (x) {
      if (x.id && x.id !== m.id && x.enabled) {
        chain = chain.then(function () {
          return call("svc_patch", {
            service: "workspace", path: "/api/models/" + encodeURIComponent(x.id), body: { enabled: false }
          }).catch(function () { /* 关旧模型失败不阻塞 */ });
        });
      }
    });
    chain.then(function () {
      return call("svc_patch", {
        service: "workspace", path: "/api/models/" + encodeURIComponent(m.id), body: { enabled: true }
      });
    }).then(function (r) {
      if (r && r.ok === false) { toast("切换失败：" + (r.error || "没切换上")); return; }
      toast("已切换到「" + label + "」");
      refreshModelCard();
      refreshRunCard();
    }).catch(function (e) { toast("切换失败：" + msgOf(e)); });
  }

  // ================= 6. 工具弹层(#cWeb) =================
  var popEl = null;
  var TOOL_ITEMS = [
    { label: "查记忆", prompt: "帮我查一下记忆里关于「」的内容", caretBack: 4 },
    { label: "看小窝", prompt: "帮我看看鱼的小窝现在怎么样了" },
    { label: "QQ 白名单", prompt: "帮我看看 QQ 白名单里有哪些人" },
    { label: "B站投稿", prompt: "帮我整理一下 B 站最近的投稿数据" }
  ];
  function closeToolPop() {
    if (popEl && popEl.parentNode) popEl.parentNode.removeChild(popEl);
    popEl = null;
  }
  function openToolPop() {
    if (popEl) { closeToolPop(); return; }
    if (!appReady()) return;
    var anchor = $("cWeb");
    if (!anchor) return;
    var pop = document.createElement("div");
    pop.className = "cPop";
    TOOL_ITEMS.forEach(function (t) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "cPopItem";
      b.textContent = t.label;
      b.addEventListener("click", function () {
        closeToolPop();
        insertAtCaret(t.prompt, t.caretBack || 0);
        showHint("已填好提示词，补充一下就能发送", true);
      });
      pop.appendChild(b);
    });
    document.body.appendChild(pop);
    var r = anchor.getBoundingClientRect();
    var w = pop.offsetWidth, h = pop.offsetHeight;
    var left = r.left, top = r.top - h - 8;
    if (left + w > window.innerWidth - 12) left = window.innerWidth - w - 12;
    if (left < 12) left = 12;
    if (top < 12) top = r.bottom + 8;
    pop.style.left = Math.round(left) + "px";
    pop.style.top = Math.round(top) + "px";
    popEl = pop;
  }

  // ================= 7. 首页跳转 / 快捷操作 =================
  function focusWsInput() {
    setTimeout(function () {
      var i = $("wsInput") || $("homeAsk");
      if (i) { try { i.focus(); } catch (e) {} }
    }, 60);
  }
  function runHeroAct(act) {
    if (act === "chat") { switchView("workspace"); focusWsInput(); }
    else if (act === "file") { pickFile("any", "文件", false); switchView("workspace"); }
    else if (act === "life") { switchView("fish"); }
    else if (act === "more") { switchView("settings"); }
  }
  function runQuickAct(act) {
    if (act === "newchat") { switchView("workspace"); focusWsInput(); }
    else if (act === "openfile") { pickFile("any", "文件", true); }
    else if (act === "switchmodel") { openModelDrop(); }
    else if (act === "logs") { switchView("cmds"); }
  }
  function bindClicks() {
    document.addEventListener("click", function (e) {
      var t = e.target;
      if (!t) return;
      var entry = closest(t, ".entryCard[data-p], #homeScene[data-p]");
      if (entry) { e.preventDefault(); switchView(entry.getAttribute("data-p")); return; }
      var setEntry = closest(t, ".setEntry[data-goto]");
      if (setEntry) { e.preventDefault(); switchView(setEntry.getAttribute("data-goto")); return; }
      var tank = closest(t, "#tankCard[data-goto]");
      if (tank) {
        // app.js 已经给 #tankCard 绑过点击(并加了 role=button),这里只兜底,避免双跳
        if (tank.getAttribute("role") === "button") return;
        e.preventDefault(); switchView(tank.getAttribute("data-goto")); return;
      }
      var chip = closest(t, ".heroChip[data-act]");
      if (chip) { e.preventDefault(); runHeroAct(chip.getAttribute("data-act")); return; }
      var qrow = closest(t, ".qrow[data-act]");
      if (qrow) { e.preventDefault(); runQuickAct(qrow.getAttribute("data-act")); return; }
    });
  }

  // ================= 8. 快捷键 =================
  function isEditable(el) {
    if (!el) return false;
    var tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    return !!el.isContentEditable;
  }
  function onKeyDown(e) {
    if (!e) return;
    if (paletteOpen) {
      if (e.key === "Escape") { e.preventDefault(); closePalette(); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); moveSel(1); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); moveSel(-1); return; }
      if (e.key === "Enter") { e.preventDefault(); executeItem(currentItems()[selIdx]); return; }
    }
    if (e.key === "Escape") { closeModelDrop(); closeToolPop(); return; }
    if (!appReady()) return;
    if (!(e.ctrlKey || e.metaKey)) return;
    if (isEditable(e.target)) return; // 输入框/文本域里不抢快捷键
    var k = String(e.key || "").toLowerCase();
    if (k === "k") { e.preventDefault(); openPalette((($("globalSearch") || {}).value) || "", false); return; }
    if (k === "n") { e.preventDefault(); switchView("workspace"); focusWsInput(); return; }
    if (k === "o") { e.preventDefault(); pickFile("any", "文件", true); return; }
    if (k === "m") { e.preventDefault(); openModelDrop(); return; }
    if (k === "l") { e.preventDefault(); switchView("cmds"); return; }
  }
  // 点面板/弹层外面关掉
  function onPointerDown(e) {
    var t = e.target;
    if (!t) return;
    if (paletteOpen) {
      var box = document.querySelector("#palette .paletteBox"), top = $("topSearch");
      if (!(box && box.contains(t)) && !(top && top.contains(t))) closePalette();
    }
    if (dropEl && !dropEl.contains(t)) {
      var chip = $("cModel");
      if (!(chip && chip.contains(t))) closeModelDrop();
    }
    if (popEl && !popEl.contains(t)) {
      var web = $("cWeb");
      if (!(web && web.contains(t))) closeToolPop();
    }
  }

  // ================= 9. 返回设置 chip =================
  var SUB_VIEWS = /^(memory|mood|persona|account|nodes|cmds|setup)$/;
  function syncBackChip(view) {
    var btn = $("btnBackSettings");
    if (!btn) return;
    btn.hidden = !SUB_VIEWS.test(String(view || ""));
  }

  // ================= 10. 启动 =================
  var lastHomeRefresh = 0;
  function refreshAll() {
    var now = Date.now();
    if (now - lastHomeRefresh < 1500) return;
    lastHomeRefresh = now;
    refreshModelCard();
    refreshRunCard();
  }
  function init() {
    var ta = $("homeAsk");
    if (ta) {
      ta.addEventListener("input", autosize);
      ta.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" || e.shiftKey) return;
        if (e.isComposing) return; // 中文输入法选词中,不发送
        e.preventDefault(); sendHome();
      });
      autosize();
    }
    var send = $("cSend"); if (send) send.addEventListener("click", sendHome);
    var attach = $("cAttach"); if (attach) attach.addEventListener("click", function () { pickFile("any", "文件", true); });
    var image = $("cImage"); if (image) image.addEventListener("click", function () { pickFile("image", "图片", true); });
    var code = $("cCode"); if (code) code.addEventListener("click", insertCode);
    var web = $("cWeb"); if (web) web.addEventListener("click", function (e) { e.stopPropagation(); openToolPop(); });
    var model = $("cModel"); if (model) model.addEventListener("click", function (e) { e.stopPropagation(); openModelDrop(); });
    var top = $("topSearch");
    if (top) top.addEventListener("click", function () {
      var gs = $("globalSearch");
      openPalette(gs ? gs.value : "", true);
      if (gs) { try { gs.focus(); } catch (e) {} }
    });
    var gs = $("globalSearch"); if (gs) gs.addEventListener("input", function () { onSearchInput(gs); });
    var pi = $("paletteInput"); if (pi) pi.addEventListener("input", function () { onSearchInput(pi); });
    var list = $("paletteList");
    if (list) list.addEventListener("click", function (e) {
      var row = closest(e.target, ".pRow");
      if (!row) return;
      var idx = parseInt(row.getAttribute("data-idx"), 10);
      if (isNaN(idx)) return;
      executeItem(currentItems()[idx]);
    });
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    var back = $("btnBackSettings"); if (back) back.addEventListener("click", function () { switchView("settings"); });
    bindClicks();
    // 先画一版真实兜底:空 sparkline 细基线 + 未启用模型状态
    renderSpark([0, 0, 0, 0, 0, 0, 0]);
    applyModelStatus({ enabled: false }, lastModels);
    syncBackChip((app() && app().state ? app().state.view : "") || "home");
    if (appReady()) refreshAll();
  }
  // 数据加载完 / 切页时刷新
  window.addEventListener("ruo:data", function () {
    try {
      if (!app()) return;
      var st = app().state || {};
      if (st.ws) { lastStatus = st.ws; applyModelStatus(st.ws, lastModels); }
      else refreshModelCard();
      applyRunCard(st.overview, st.cmds, st.nodes); // 先用已有数据画一版
      refreshRunCard();                            // 再按 200 条命令重算 7 天曲线
    } catch (e) { /* 永不抛 */ }
  });
  window.addEventListener("ruo:view", function (e) {
    try {
      var v = (e && e.detail) || ((app() && app().state) ? app().state.view : "");
      syncBackChip(v);
      closePalette(); closeModelDrop(); closeToolPop();
      if (v === "home") refreshAll();
    } catch (x) { /* 永不抛 */ }
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
