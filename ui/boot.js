/* Rivulet 若 · 启动脚本 v4
 * 从 index.html 的内联脚本搬出来(CSP 不允许 inline script),
 * 负责:窗口控制、拖动标题栏、DPR 缩放、鼠标光斑、吉祥物注入、小补丁。
 */
(function () {
  'use strict';

  var TAURI = window.__TAURI__ || null;
  var winApi = null;
  try {
    if (TAURI && TAURI.window && TAURI.window.getCurrentWindow) winApi = TAURI.window.getCurrentWindow();
    else if (TAURI && TAURI.webviewWindow && TAURI.webviewWindow.getCurrentWebviewWindow) winApi = TAURI.webviewWindow.getCurrentWebviewWindow();
  } catch (e) { winApi = null; }

  function callWin(method) {
    if (!winApi || typeof winApi[method] !== 'function') return;
    try { winApi[method](); } catch (e) { }
  }


  // ---------- 侧栏展开/收起(记住选择) ----------
  (function railToggle() {
    var root = document.documentElement;
    var btn = document.getElementById('btnRailToggle');
    try {
      var saved = localStorage.getItem('ruo.rail');
      if (saved === 'narrow') root.removeAttribute('data-rail');
      else if (saved === 'wide') root.setAttribute('data-rail', 'wide');
    } catch (e) { }
    if (!btn) return;
    btn.addEventListener('click', function () {
      var wide = root.getAttribute('data-rail') === 'wide';
      if (wide) root.removeAttribute('data-rail');
      else root.setAttribute('data-rail', 'wide');
      try { localStorage.setItem('ruo.rail', wide ? 'narrow' : 'wide'); } catch (e) { }
    });
  })();

  // ---------- 卡片指针光斑(只更新 CSS 变量,不重排) ----------
  (function specular() {
    var last = null, raf = 0, px = 0, py = 0;
    function apply() {
      raf = 0;
      var el = last; if (!el || !el.isConnected) return;
      var r = el.getBoundingClientRect();
      el.style.setProperty('--mx', (px - r.left) + 'px');
      el.style.setProperty('--my', (py - r.top) + 'px');
    }
    document.addEventListener('pointermove', function (e) {
      px = e.clientX; py = e.clientY;
      var el = e.target && e.target.closest ? e.target.closest('.qcard,.stat,.pulse') : null;
      if (el !== last) {
        if (last) { last.style.removeProperty('--mx'); last.style.removeProperty('--my'); }
        last = el;
      }
      if (el && !raf) raf = requestAnimationFrame(apply);
    }, { passive: true });
  })();

  // ---------- 错误收集(验收/排查用) ----------
  window.__ruoErrors = [];
  window.addEventListener('error', function (e) {
    try { var mm = e.message || ''; if (mm.indexOf('ResizeObserver') < 0) window.__ruoErrors.push({ t: 'error', m: mm + ' @ ' + (e.filename || '') + ':' + (e.lineno || 0) }); } catch (x) { }
  });
  window.addEventListener('unhandledrejection', function (e) {
    try { window.__ruoErrors.push({ t: 'rejection', m: String((e.reason && (e.reason.message || e.reason)) || '') }); } catch (x) { }
  });

  // ---------- 自定义窗框 ----------
  var btnMin = document.getElementById('btnWinMin');
  var btnMax = document.getElementById('btnWinMax');
  var btnClose = document.getElementById('btnWinClose');
  var titlebar = document.getElementById('titlebar');

  if (btnMin) btnMin.addEventListener('click', function () { callWin('minimize'); });
  if (btnMax) btnMax.addEventListener('click', function () { callWin('toggleMaximize'); });
  if (btnClose) btnClose.addEventListener('click', function () { callWin('close'); });

  // 拖动兜底:新壳没有 #titlebar,用顶部栏/侧栏空白处拖动
  (function dragFallback() {
    var tb = document.getElementById('topbar');
    if (!tb) return;
    tb.addEventListener('pointerdown', function (e) {
      if (e.target.closest && e.target.closest('button, input, a')) return;
      if (winApi && winApi.startDragging) { e.preventDefault(); try { winApi.startDragging(); } catch (err) { } }
    });
    tb.addEventListener('dblclick', function (e) {
      if (e.target.closest && e.target.closest('button, input, a')) return;
      callWin('toggleMaximize');
    });
  })();

  // 双击标题栏 = 最大化 / 还原
  if (titlebar) {
    titlebar.addEventListener('dblclick', function (e) {
      // 别把按钮上的双击也当成标题栏双击
      if (e.target.closest && e.target.closest('button, input, .winbtns, .tb-user')) return;
      callWin('toggleMaximize');
    });
  }

  // 无边框窗口在 WebView2 里仍然可以拖:给空白区补 app-region
  if (titlebar) {
    titlebar.addEventListener('pointerdown', function (e) {
      if (e.target.closest && e.target.closest('button, input, a, .tb-user, .winbtns')) return;
      if (winApi && winApi.startDragging) { e.preventDefault(); try { winApi.startDragging(); } catch (err) { } }
    });
  }

  // 最大化状态:换图标/按压缩放
  if (winApi && typeof winApi.onResized === 'function') {
    var syncMax = function () {
      try {
        Promise.resolve(winApi.isMaximized()).then(function (m) {
          document.documentElement.setAttribute('data-maximized', m ? '1' : '0');
          if (btnMax) btnMax.title = m ? '还原' : '最大化';
        }).catch(function () { });
      } catch (e) { }
    };
    syncMax();
    winApi.onResized(syncMax);
  }

  // ---------- 整体等比缩放(v11.1) ----------
  // 设计基准 1360 宽;窗口多宽,整套界面就按同一比例放大/缩小。
  // 只放大、不缩小:缩小会让文字低于原生字号(100% DPI 下 13.5px→11px),
  // 1px 描边落到半像素,视觉发虚。所以下限锁 1.0,最小窗口 = 设计尺寸。
  (function fitZoom() {
    var BASE = 1360;            // 设计基准宽度(tauri.conf.json 最小窗口宽)
    var last = 0;
    function apply() {
      var w = window.innerWidth || BASE;
      var s = w / BASE;
      s = Math.max(1, Math.min(2.4, s));   // 只放大不缩小
      s = Math.round(s * 1000) / 1000;
      if (Math.abs(s - last) < 0.002) return;
      last = s;
      document.documentElement.style.zoom = s;
      document.documentElement.setAttribute('data-zoom', String(s));
      // zoom 会让 vh 单位偏大,这里给出真实的 CSS 像素高度供布局用
      document.documentElement.style.setProperty('--vhpx', (window.innerHeight / s) + 'px');
    }
    apply();
    var t = 0;
    window.addEventListener('resize', function () {
      clearTimeout(t);
      t = setTimeout(apply, 60);
    });
    // WebView2 里最大化/还原有时不触发 resize,补一次轮询兜底
    var lastW = window.innerWidth;
    setInterval(function () {
      if (window.innerWidth !== lastW) { lastW = window.innerWidth; apply(); }
    }, 500);
  })();

  // ---------- 鼠标光斑 ----------
  (function glow() {
    var el = document.getElementById('mouseGlow');
    if (!el) return;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var noMouse = !window.matchMedia || !window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    if (reduce || noMouse) { el.style.display = 'none'; return; }
    var tx = window.innerWidth * 0.5, ty = window.innerHeight * 0.32;
    var cx = tx, cy = ty, raf = 0;
    function loop() {
      raf = 0;
      cx += (tx - cx) * 0.16; cy += (ty - cy) * 0.16;
      el.style.setProperty('--gx', cx.toFixed(1) + 'px');
      el.style.setProperty('--gy', cy.toFixed(1) + 'px');
      if (Math.abs(tx - cx) > 0.5 || Math.abs(ty - cy) > 0.5) raf = requestAnimationFrame(loop);
    }
    function move(e) {
      tx = e.clientX; ty = e.clientY;
      if (!raf) raf = requestAnimationFrame(loop);
    }
    window.addEventListener('pointermove', move, { passive: true });
    el.style.setProperty('--gx', cx + 'px');
    el.style.setProperty('--gy', cy + 'px');
  })();

  // ---------- 大肥鱼/角色注入 ----------
  (function fishInject() {
    function boot() {
      if (window.RivuletFish) {
        window.RivuletFish.inject('#wsFish', { size: 'lg', state: 'idle' });
      }
      if (window.RivuletAvatar) {
        var qq = document.getElementById('qqFish'); if (qq) qq.innerHTML = window.RivuletAvatar.html({ size: 'lg' });
        var bl = document.getElementById('biliFish'); if (bl) bl.innerHTML = window.RivuletAvatar.html({ size: 'lg' });
        var sa = document.getElementById('sceneAvatar'); if (sa) sa.innerHTML = window.RivuletAvatar.html({ size: 'md' });
        window.RivuletAvatar.mount();
      }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
  })();

  // ---------- 主题按钮文案/图标同步(图标由 CSS 控制,这里只补 title) ----------
  function syncThemeUi() {
    var btn = document.getElementById('btnTheme');
    if (!btn) return;
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    btn.setAttribute('title', dark ? '切换到白天温室' : '切换到夜晚温室');
    btn.setAttribute('aria-label', btn.getAttribute('title'));
  }
  document.addEventListener('DOMContentLoaded', syncThemeUi);
  var themeBtn = document.getElementById('btnTheme');
  if (themeBtn) themeBtn.addEventListener('click', function () { setTimeout(syncThemeUi, 0); });

  // ---------- 登录页时钟 ----------
  (function loginClock() {
    function tick() {
      var el = document.getElementById('loginClock');
      if (!el) return;
      var d = new Date(); var p = function (n) { return (n < 10 ? '0' : '') + n; };
      el.textContent = p(d.getHours()) + ':' + p(d.getMinutes());
    }
    tick(); setInterval(tick, 10000);
  })();

  // ---------- 页面滚动重置(切页后回到顶部) ----------
  var lastView = '';
  setInterval(function () {
    var crumb = document.getElementById('tbCrumb');
    var label = crumb ? crumb.textContent : '';
    if (label && label !== lastView) {
      lastView = label;
      var v = document.getElementById('view');
      if (v) v.scrollTop = 0;
    }
  }, 400);
})();
