/* Rivulet 若 · 鱼的小窝 UI(照朋友版重做:剖开的小楼 + 状态卡 + 右栏)
   数据: home_snapshot() / home_action({body}) —— EXE 内本地引擎,不连 HTTP。
   布局: 两层叠起来(2F 上 / 1F 下)+ 屋顶;房间按类型上墙纸;家具按格子画;角色点阵。
   动画: 轮询只取 path/path_started,位置用 rAF 按 dt 连续插值(dt 上限 100ms)。 */
(function () {
  'use strict';

  var W = 860, H = 720;
  var COLS = 12, ROWS = 8;
  // 250ms 会每秒序列化 4 次完整场景(家具/角色/事件),低配机器明显发卡。
  // 500ms 视觉上一样顺,CPU 减半。
  var POLL_MS = 500, MAX_DT = 0.1;

  var CROPS = {
    tomato: ['番茄', '#D95F4E'], bokchoy: ['小白菜', '#7FB069'], carrot: ['胡萝卜', '#E2914A'],
    sunflower: ['向日葵', '#F2C84B'], daisy: ['小雏菊', '#EFEAD6'], potato: ['土豆', '#A97C4F'],
    pumpkin: ['南瓜', '#E89445']
  };
  var JOB_CN = { water: '浇花', harvest: '收获', plant: '播种', tidy: '整理', read: '看书', wander: '随便走走', sleep: '睡觉', '': '空闲' };
  var NAMES = { jingyu: '鲸鱼娘', claude: 'Claude', chatgpt: 'ChatGPT', nini: '泥泥', huahua: '花花', haihai: '海海', weiwei: '薇薇' };

  var $ = function (id) { return document.getElementById(id); };
  var cv, ctx, pop, toastEl, busyEl;
  var snap = null, view = 'house', mounted = false, rafLast = 0, pollTimer = null;
  var hit = { chars: [], furniture: [], plots: [] };
  var selected = null, hoverId = null, hoverCell = null, actionBusy = false;
  var anim = new Map(), tick = 0, pendingBuy = null;

  function invoke(cmd, args) {
    var t = window.__TAURI__ && window.__TAURI__.core;
    if (!t) return Promise.reject(new Error('需要在 Rivulet 若 EXE 内运行'));
    return t.invoke(cmd, args || {});
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    if (ctx.roundRect) { ctx.roundRect(x, y, w, h, r); return; }
    ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath();
  }
  function px(x, y, w, h, c) { ctx.fillStyle = c; ctx.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h)); }
  function ell(cx, cy, rx, ry, c, a) {
    ctx.save(); ctx.globalAlpha = a == null ? 1 : a; ctx.fillStyle = c;
    ctx.beginPath(); ctx.ellipse(cx, cy, Math.max(.5, rx), Math.max(.5, ry), 0, 0, Math.PI * 2); ctx.fill(); ctx.restore();
  }
  function mixc(a, b, t) {
    function rgb(h) { h = String(h).replace('#', ''); return [parseInt(h.substr(0, 2), 16), parseInt(h.substr(2, 2), 16), parseInt(h.substr(4, 2), 16)]; }
    var A = rgb(a), B = rgb(b);
    function hx(v) { var s = Math.max(0, Math.min(255, Math.round(v))).toString(16); return s.length < 2 ? '0' + s : s; }
    return '#' + hx(A[0] + (B[0] - A[0]) * t) + hx(A[1] + (B[1] - A[1]) * t) + hx(A[2] + (B[2] - A[2]) * t);
  }

  // ---------------- 数据 ----------------
  function refresh(force) {
    return invoke('home_snapshot').then(function (s) {
      if (!s) return;
      var first = !snap;
      snap = s;
      syncAnim();
      updateHud();
      renderSide();
      if (first) drawAvatar();
      render();
      renderExtraPage();
    }).catch(function (e) {
      if (!snap) drawOffline(e && e.message ? e.message : String(e));
    });
  }
  function act(body) {
    if (actionBusy) return Promise.resolve();
    actionBusy = true;
    if (busyEl) busyEl.textContent = '处理中…';
    return invoke('home_action', { body: body }).then(function (r) {
      if (r && r.ok === false) toast(String(r.error || '操作失败'));
      return refresh(true);
    }).catch(function (e) {
      toast('操作失败:' + (e && e.message ? e.message : e));
    }).then(function () {
      actionBusy = false;
      if (busyEl) busyEl.textContent = '';
    });
  }
  function toast(msg) {
    if (!toastEl) return;
    toastEl.textContent = String(msg || '');
    toastEl.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { toastEl.hidden = true; }, 2600);
  }

  // ---------------- 动画 ----------------
  function syncAnim() {
    (snap && snap.characters || []).forEach(function (c) {
      var a = anim.get(c.id);
      var hired = !!c.hired, place = c.place || 'house';
      if (!a) {
        anim.set(c.id, { x: c.x, y: c.y, floor: c.floor, goals: [], visible: hired, moving: false, place: place });
        return;
      }
      a.visible = hired;
      a.place = place;
      if (a.floor !== c.floor) { a.floor = c.floor; a.x = c.x; a.y = c.y; a.goals = []; }
      var path = c.path || [];
      if (path.length) {
        var f0 = path[0];
        if (!a.goals.length || Math.abs(a.goals[0].x - (f0.x + 0.5)) > 0.01 || Math.abs(a.goals[0].y - (f0.y + 0.5)) > 0.01) {
          a.goals = path.map(function (p) { return { x: p.x + 0.5, y: p.y + 0.5 }; });
        }
      } else if (!a.goals.length) {
        a.soft = { x: c.x, y: c.y };
      }
      a.moving = !!a.goals.length;
    });
  }
  function stepAnim(dt) {
    var speed = 2.6;
    anim.forEach(function (a) {
      if (a.goals.length) {
        var g = a.goals[0];
        var dx = g.x - a.x, dy = g.y - a.y, d = Math.hypot(dx, dy);
        var step = speed * dt;
        if (d <= step) { a.x = g.x; a.y = g.y; a.goals.shift(); if (!a.goals.length) a.moving = false; }
        else { a.x += dx / d * step; a.y += dy / d * step; a.moving = true; }
      } else if (a.soft) {
        var sx = a.soft.x - a.x, sy = a.soft.y - a.y, sd = Math.hypot(sx, sy);
        if (sd > 0.02) { var st = Math.min(sd, speed * 0.8 * dt); a.x += sx / sd * st; a.y += sy / sd * st; }
      }
    });
  }

  // ---------------- 布局 ----------------
  var PAD = 12;
  // 所有楼层尺寸只从这里取,禁止各处写死
  function metrics(c) {
    var roof = Math.round(c * 2), slab = Math.round(c * 0.33), band = Math.round(c * 0.6);
    return { roof: roof, slab: slab, band: band, total: roof + slab + c * ROWS * 2 };
  }
  // 按可用宽高反推格宽:先粗估,再按 metrics 的整数高度回缩,保证两层都装得下
  function calcC(availW, availH) {
    var c = Math.min((availW - 48) / COLS, (availH - PAD * 2 - 24) / (ROWS * 2 + 2.6));
    c = Math.max(6, Math.floor(c));
    for (var i = 0; i < 8; i++) {
      var m = metrics(c);
      if (m.total <= availH - PAD * 2 - 24) break;
      c = Math.max(6, c - 1);
    }
    return c;
  }
  function hg() {
    var c = calcC(W, H), m = metrics(c);
    var houseH = c * ROWS * 2 + m.slab;
    var top = m.roof + Math.max(0, (H - m.roof - PAD * 2 - houseH) / 2);
    return { c: c, x0: (W - c * COLS) / 2, y0: top, m: m, roofH: m.roof, slab: m.slab, band: m.band };
  }
  function fox() { return hg().x0; }
  function fy(f) { var g = hg(); return f === 1 ? g.y0 + g.c * ROWS + g.slab : g.y0; }

  function layoutOf() { return (snap && snap.layout) || null; }
  function roomsOf(f) {
    var L = layoutOf();
    if (L && L.rooms) {
      return L.rooms.filter(function (r) { return r.floor === f; })
        .map(function (r) { return [r.x0, r.y0, r.x1, r.y1, r.name, r.key]; });
    }
    return [];
  }
  function catalogOf() { var L = layoutOf(); return (L && L.catalog) ? L.catalog : {}; }
  function stairSpec() { var L = layoutOf(); return (L && L.stair) ? L.stair : { x0: 11, y0: 0, x1: 11, y1: 2 }; }
  function windowSpecs(f) {
    var L = layoutOf();
    if (!L || !L.windows) return [];
    return L.windows.filter(function (w) { return (w.floor || 1) === f; });
  }
  function doorSpec() { var L = layoutOf(); return (L && L.door) || null; }

  // ---------------- 家具字符画(8px/格, 侧视娃娃屋风格) ----------------
  function furnArt(kind, iw, ih) {
    var w = iw * 8, h = ih * 8, g = [];
    for (var y = 0; y < h; y++) { g.push([]); for (var x = 0; x < w; x++) g[y].push('.'); }
    function R(x0, y0, x1, y1, ch) {
      for (var yy = Math.max(0, y0); yy < Math.min(h, y1); yy++)
        for (var xx = Math.max(0, x0); xx < Math.min(w, x1); xx++) g[yy][xx] = ch;
    }
    function O(x0, y0, x1, y1) { R(x0, y0, x1, y0 + 1, 'o'); R(x0, y1 - 1, x1, y1, 'o'); R(x0, y0, x0 + 1, y1, 'o'); R(x1 - 1, y0, x1, y1, 'o'); }
    function box(x0, y0, x1, y1, ch) { R(x0, y0, x1, y1, ch); O(x0, y0, x1, y1); }
    var i, yy, xx;
    if (kind === 'bed') {
      box(0, 0, w, h, '5');
      R(1, 1, w - 1, 5, '6');
      R(2, 2, w - 2, 5, '4');                       // 枕头
      R(1, 6, w - 1, h - 3, '1');                   // 被子
      R(1, 6, w - 1, 7, '2');
      for (yy = 9; yy < h - 4; yy += 4) { R(1, yy, w - 1, yy + 1, '2'); R(1, yy + 2, w - 1, yy + 3, '3'); }
      R(1, h - 4, w - 1, h - 2, '3');
    } else if (kind === 'sofa') {
      box(0, 0, w, h, '1');
      R(1, 1, w - 1, 3, '3');
      R(1, 1, 3, h - 1, '3'); R(w - 3, 1, w - 1, h - 1, '3');
      R(4, 3, Math.floor(w / 2) - 1, h - 2, '1'); R(4, 3, Math.floor(w / 2) - 1, 4, '2');
      R(Math.floor(w / 2) + 1, 3, w - 4, h - 2, '1'); R(Math.floor(w / 2) + 1, 3, w - 4, 4, '2');
      R(Math.floor(w / 2) - 1, 3, Math.floor(w / 2) + 1, h - 2, 'o');
      R(4, h - 2, w - 4, h - 1, '3');
    } else if (kind === 'tv') {
      R(3, 0, w - 3, 6, 'o');
      R(4, 1, w - 4, 5, '4');
      R(4, 1, w - 4, 2, '2');
      R(5, 6, w - 5, 8, '3');
      R(7, 7, w - 7, 8, '5');
    } else if (kind === 'coffee') {
      R(0, 1, w, 5, 'o'); R(1, 2, w - 1, 4, '5'); R(1, 2, w - 1, 3, '2');
      R(1, 5, 3, h, '6'); R(w - 3, 5, w - 1, h, '6');
      R(w / 2 - 2, 0, w / 2, 2, '4');
    } else if (kind === 'rug') {
      R(0, 0, w, h, 'o'); R(1, 1, w - 1, h - 1, '1');
      R(1, 1, w - 1, 2, '2');
      for (xx = 3; xx < w - 3; xx += 5) { R(xx, 4, xx + 2, 5, '4'); R(xx, h - 6, xx + 2, h - 5, '4'); }
      for (yy = 6; yy < h - 6; yy += 5) { R(3, yy, 4, yy + 2, '4'); R(w - 5, yy, w - 4, yy + 2, '4'); }
    } else if (kind === 'plant') {
      R(2, 5, 6, 8, '5'); O(2, 5, 6, 8);
      R(1, 2, 4, 5, '1'); R(4, 1, 7, 5, '2'); R(2, 0, 5, 3, '1'); R(5, 3, 7, 5, '1');
    } else if (kind === 'dlamp') {
      R(3, 3, 5, 8, '6');
      R(1, 0, 7, 3, '4'); O(1, 0, 7, 3); R(2, 1, 6, 2, '7');
      R(1, 7, 7, 8, '3');
    } else if (kind === 'lamp') {
      R(2, 3, 6, 4, '6'); R(1, 0, 7, 3, '4'); O(1, 0, 7, 3); R(2, 1, 6, 2, '7');
      R(1, 7, 7, 8, '3');
    } else if (kind === 'catbed') {
      R(0, 2, w, 8, '5'); O(0, 2, w, 8);
      R(1, 2, w - 1, 5, '2'); R(2, 1, w - 2, 4, '1');
      R(3, 3, 4, 4, 'o'); R(w - 5, 3, w - 4, 4, 'o');
    } else if (kind === 'fridge') {
      box(0, 0, w, h, '2');
      R(1, 3, w - 1, 4, 'o');
      R(w - 3, 1, w - 2, 3, '3'); R(w - 3, 4, w - 2, 6, '3');
    } else if (kind === 'stove') {
      box(0, 0, w, h, '3');
      R(2, 1, 6, 5, '6'); R(9, 1, 13, 5, '6');
      R(3, 2, 5, 4, '4'); R(10, 2, 12, 4, '4');
      R(1, 6, w - 1, 7, 'o');
    } else if (kind === 'sink') {
      box(0, 0, w, h, '2');
      R(2, 2, 6, 6, '3'); R(3, 3, 5, 5, '2');
      R(4, 0, 6, 2, '5'); R(6, 0, 7, 1, '5');
    } else if (kind === 'dtable') {
      R(0, 1, w, 5, 'o'); R(1, 2, w - 1, 4, '5'); R(1, 2, w - 1, 3, '2');
      R(1, 5, 3, h, '6'); R(w - 3, 5, w - 1, h, '6');
      R(3, 1, 5, 3, '4'); R(w - 5, 1, w - 3, 3, '4');
    } else if (kind === 'desk') {
      R(0, 3, w, 6, 'o'); R(1, 4, w - 1, 5, '5');
      R(1, 6, 3, h, '6'); R(w - 3, 6, w - 1, h, '6');
      R(3, 0, 8, 4, 'o'); R(4, 1, 7, 3, '4');
    } else if (kind === 'chair') {
      R(2, 0, 7, 4, '5'); O(2, 0, 7, 4);
      R(1, 3, 8, 6, '5'); R(2, 4, 7, 5, '2'); O(1, 3, 8, 6);
      R(1, 6, 2, 8, '6'); R(7, 6, 8, 8, '6');
    } else if (kind === 'shoe') {
      box(0, 3, w, h, '5');
      R(3, 4, 4, 7, 'o'); R(5, 5, 6, 6, '3');
      R(0, 2, w, 3, '2');
    } else if (kind === 'nstand') {
      box(0, 1, w, h, '5');
      R(2, 2, 7, 3, 'o'); R(4, 3, 5, 4, '3');
      R(3, 0, 6, 2, '4');
    } else if (kind === 'closet') {
      box(0, 0, w, h, '5');
      R(Math.floor(w / 2) - 1, 1, Math.floor(w / 2) + 1, h - 1, 'o');
      R(2, 2, 3, 4, '3'); R(w - 4, 2, w - 3, 4, '3');
      R(1, 1, w - 1, 2, '2');
    } else if (kind === 'shelf') {
      box(0, 0, w, h, '5');
      R(1, 3, w - 1, 4, '6'); R(1, 7, w - 1, 8, '6');
      R(1, 1, 3, 3, '1'); R(3, 1, 5, 3, '2'); R(5, 1, 7, 3, '4');
      R(1, 4, 3, 7, '4'); R(3, 4, 4, 7, '1'); R(4, 4, 6, 7, '2');
    } else if (kind === 'bean') {
      R(1, 2, w - 1, h, '1'); O(1, 2, w - 1, h);
      R(2, 1, w - 2, 4, '2'); R(2, 4, w - 2, 5, 'o');
    } else {
      box(0, 0, w, h, '1');
      R(1, 1, w - 1, 2, '2');
    }
    return g.map(function (row) { return row.join(''); });
  }
  var FACC = {
    bed: { '1': '#5B7A9E', '2': '#8099B5', '3': '#42596F' },
    sofa: { '1': '#5C6F8C', '2': '#8092A8', '3': '#3F5068' },
    rug: { '1': '#6B5A72', '2': '#7E6D86', '3': '#55465C', '4': '#8A7690' },
    tv: { '1': '#3B4552', '2': '#C9E6F4', '3': '#242B34', '4': '#9FD2E8' },
    plant: { '1': '#4E8C4A', '2': '#72B265', '5': '#C4713F' },
    fridge: { '1': '#C9D6DE', '2': '#DCE7EE', '3': '#A9B9C4' },
    stove: { '1': '#4A4F57', '2': '#5F656E', '3': '#3A3E45', '6': '#E08B3C' },
    sink: { '1': '#C9D6DE', '2': '#E4EDF2', '3': '#B3C2CC', '5': '#B9C6CE' },
    bean: { '1': '#C99A6B', '2': '#DFB88C' },
    catbed: { '1': '#C9A87C', '2': '#E3C9A6', '5': '#B07B4C' },
    dlamp: { '4': '#F6D98A', '7': '#FFF4CE', '6': '#8A6A4E' },
    lamp: { '4': '#F6D98A', '7': '#FFF4CE', '6': '#8A6A4E' },
    coffee: { '5': '#9A6E44', '6': '#6E4A2C' },
    dtable: { '5': '#9A6E44', '6': '#6E4A2C' },
    chair: { '5': '#9A6E44', '6': '#6E4A2C' },
    desk: { '5': '#9A6E44', '6': '#6E4A2C', '4': '#BFD8EA' },
    closet: { '5': '#8A6142', '2': '#A87B56', '3': '#6B4A30' },
    shoe: { '5': '#8A6142', '2': '#A87B56', '3': '#6B4A30' },
    nstand: { '5': '#8A6142', '3': '#6B4A30' },
    shelf: { '5': '#8A6142', '6': '#6B4A30', '1': '#C9564F', '2': '#6E93BF', '4': '#E4C86A' }
  };
  function furnPalette(kind) {
    var cat = catalogOf()[kind] || {};
    var base = cat.color || '#8B7A64';
    var main = mixc(base, '#FFFFFF', 0.18);
    var p = {
      '.': null,
      'o': mixc(base, '#2A2118', 0.78),
      '1': main,
      '2': mixc(main, '#FFFFFF', 0.32),
      '3': mixc(main, '#000000', 0.26),
      '4': '#F6F1E7',
      '5': '#9A6E44',
      '6': '#6E4A2C',
      '7': '#FFFFFF'
    };
    var a = FACC[kind];
    if (a) Object.keys(a).forEach(function (k) { p[k] = a[k]; });
    return p;
  }

  // ---------------- 角色点阵(11×14) ----------------
  var HER_ART = [
    '....oooooo....',
    '...o222211o...',
    '..o22111111o..',
    '.o2211111111o.',
    '.o1111111111o.',
    '.o1133333311o.',
    '.o1333333331o.',
    '.o1336336331o.',
    '.o1333333331o.',
    '.o1133333311o.',
    '..o11111111o..',
    '..o44555544o..',
    '.o4445555444o.',
    '.o4445555444o.',
    '..o44444444o..',
    '...o444444o...',
    '...o44..44o...',
    '...o66..66o...'
  ];
  var HER_COL = { o: '#0B1018', '1': '#3C6FC4', '2': '#6FA3EF', '3': '#F0D2BA', '4': '#4A86E8', '5': '#8FBCF7', '6': '#1B2530', '7': '#24406E' };
  var CUR_ART = ['ooooooooo', '.o11111o.', '.o12221o.', '..o111o..', '...o1o...', '....o....'];
  var ROLE_COL = {
    jingyu: { '4': '#4A86E8', '5': '#8FBCF7' }, claude: { '4': '#D97757', '5': '#F0A184' },
    chatgpt: { '4': '#EDEAF6', '5': '#FFFFFF' }, nini: { '4': '#E2A9A0', '5': '#F5C4BC' },
    huahua: { '4': '#E9C46A', '5': '#F6D77A' }, haihai: { '4': '#6FAEE0', '5': '#9CCBF2' },
    weiwei: { '4': '#8A6FD0', '5': '#B29AEA' }
  };
  function roleCols(c) {
    var rc = ROLE_COL[c.id] || ROLE_COL.nini;
    var col = {};
    Object.keys(HER_COL).forEach(function (k) { col[k] = HER_COL[k]; });
    col['4'] = rc['4']; col['5'] = rc['5'];
    col['7'] = mixc(col['1'], '#000000', 0.45);
    return col;
  }
  function artDraw(art, colMap, k, px0, py0, alpha) {
    ctx.save(); ctx.globalAlpha = alpha == null ? 1 : alpha;
    for (var r = 0; r < art.length; r++) {
      for (var ci = 0; ci < art[r].length; ci++) {
        var col = colMap[art[r][ci]];
        if (!col) continue;
        ctx.fillStyle = col;
        ctx.fillRect(Math.round(px0 + ci * k), Math.round(py0 + r * k), k, k);
      }
    }
    ctx.restore();
  }
  function cursorImg(cols, k, x, yBase) {
    var cc = { o: 'rgba(40,50,60,.85)', '1': '#FFFFFF', '2': cols['5'] };
    artDraw(CUR_ART, cc, k, x - Math.floor(CUR_ART[0].length * k / 2), yBase - CUR_ART.length * k);
  }

  // ---------------- 渲染 ----------------
  function render() {
    if (!ctx) return;
    if (cv && (cv.width !== W || cv.height !== H)) { cv.width = W; cv.height = H; }
    ctx.clearRect(0, 0, W, H);
    var bg = ctx.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, '#F8F3E8'); bg.addColorStop(1, '#EFE6D4');
    ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
    for (var i = 0; i < 150; i++) px((i * 123.7) % W, (i * 77.3) % H, 2, 2, 'rgba(160,138,104,.07)');
    hit = { chars: [], furniture: [], plots: [] };
    if (!snap) return;
    if (view === 'house') drawHouse();
    else if (view === 'farm') drawFarm();
    else return;
    drawHover();
    if (selected && selected.rect) drawSelection();
  }

  function drawHouse() {
    var g = hg();
    drawRoof(g);
    var list = (snap.characters || []).filter(function (c) { return c.hired && (c.place || 'house') === 'house'; });
    [2, 1].forEach(function (f) {
      drawFloor(g, f);
      ctx.save();
      ctx.beginPath();
      ctx.rect(fox() - 4, fy(f) - 2, COLS * g.c + 8, ROWS * g.c + 4);
      ctx.clip();
      list.filter(function (c) { return (c.floor || 1) === f; }).forEach(function (c) { drawCharacter(c, f); });
      ctx.restore();
    });
  }

  function drawRoof(g) {
    var c = g.c, m = g.m || metrics(c), x0 = g.x0 - 28, x1 = g.x0 + COLS * c + 28, y0 = 12, y1 = g.y0 - 6;
    var peak = Math.max(28, Math.round(m.roof * 0.72));
    var rows = Math.max(4, Math.round((y1 - y0) / 10));
    ctx.save();
    var grd = ctx.createLinearGradient(0, y0, 0, y1);
    grd.addColorStop(0, '#7A4B33'); grd.addColorStop(.55, '#5E3626'); grd.addColorStop(1, '#4A2A1E');
    ctx.beginPath();
    ctx.moveTo(x0, y1); ctx.lineTo(x0 + peak, y0); ctx.lineTo(x1 - peak, y0); ctx.lineTo(x1, y1);
    ctx.closePath();
    ctx.fillStyle = grd; ctx.fill();
    ctx.save(); ctx.clip();
    for (var i = 0; i < rows; i++) {
      var t = i / rows, yy = y0 + (y1 - y0) * t;
      var lx = x0 + peak * (1 - t), rx = x1 - peak * (1 - t);
      ctx.fillStyle = 'rgba(255,226,196,' + (0.10 - i * 0.009).toFixed(3) + ')';
      ctx.fillRect(lx, yy, rx - lx, 2);
      ctx.fillStyle = 'rgba(38,18,10,.32)';
      ctx.fillRect(lx, yy + (y1 - y0) / rows - 1, rx - lx, 1);
      for (var sx = lx + 14; sx < rx; sx += 34)
        ctx.fillRect(sx + (i % 2 ? 17 : 0), yy + 3, 1.2, (y1 - y0) / rows - 5);
    }
    ctx.restore();
    ctx.fillStyle = '#6B4030'; ctx.fillRect(x0 + peak - 2, y0 - 3, (x1 - peak) - (x0 + peak) + 4, 3);
    ctx.fillStyle = 'rgba(255,232,204,.28)'; ctx.fillRect(x0 + peak, y0 - 3, (x1 - peak) - (x0 + peak), 1);
    ctx.fillStyle = '#5B3826';
    ctx.fillRect(x0 - 10, y1 - 7, (x1 - x0) + 20, 9);
    ctx.fillStyle = '#7E5039'; ctx.fillRect(x0 - 10, y1 - 7, (x1 - x0) + 20, 2);
    var cx0 = x1 - 132;
    ctx.fillStyle = 'rgba(50,28,18,.18)'; ctx.fillRect(cx0 + 4, y0 - 2, 24, 52);
    ctx.fillStyle = '#5A3A29'; ctx.fillRect(cx0, y0 - 6, 24, 52);
    ctx.fillStyle = '#75503A'; ctx.fillRect(cx0 + 2, y0 - 6, 7, 52);
    ctx.fillStyle = '#463025'; ctx.fillRect(cx0 - 5, y0 - 14, 34, 10);
    ctx.fillStyle = '#6B4632'; ctx.fillRect(cx0 - 5, y0 - 14, 34, 3);
    ctx.restore();
  }

  var WALL = {
    living: ['#C9AE8B', 'brick'], kitchen: ['#DDD8CC', 'tiles'], hall: ['#D8C8AE', 'brick'],
    bedroom: ['#BFB3CB', 'dots'], study: ['#C6A277', 'planks']
  };

  function drawFloor(g, f) {
    var c = g.c, ox = fox(), top = fy(f);
    ctx.save();
    ctx.shadowColor = 'rgba(90,70,45,.14)'; ctx.shadowBlur = 12; ctx.shadowOffsetY = 4;
    roundRect(ox - 10, top - 10, COLS * c + 20, ROWS * c + 20, 9);
    ctx.fillStyle = '#C89A6B'; ctx.fill();
    ctx.restore();
    roundRect(ox - 5, top - 5, COLS * c + 10, ROWS * c + 10, 6);
    ctx.fillStyle = '#E6D3B4'; ctx.fill();
    roundRect(ox - 10, top - 10, COLS * c + 20, ROWS * c + 20, 9);
    ctx.strokeStyle = 'rgba(120,90,58,.55)'; ctx.lineWidth = 1.6; ctx.stroke();
    px(ox - 10, top - 10, COLS * c + 20, 2, 'rgba(255,240,214,.35)');

    roomsOf(f).forEach(function (r) {
      var x1 = ox + r[0] * c, y1 = top + r[1] * c;
      var x2 = ox + (r[2] + 1) * c, y2 = top + (r[3] + 1) * c;
      drawRoomWall(r[5], x1, y1, x2 - x1, y2 - y1);
    });

    // 背墙带(娃娃屋剖面感),高度跟格子走
    var BAND = g.m ? g.m.band : metrics(c).band;
    px(ox, top, COLS * c, BAND, '#E2D2B8');
    px(ox, top, COLS * c, 2, 'rgba(255,250,235,.7)');
    px(ox, top + BAND - 2, COLS * c, 2, 'rgba(102,78,52,.32)');

    var st = stairSpec();
    for (var yy = st.y0; yy <= st.y1; yy++) {
      var yy0 = top + yy * c;
      ctx.save(); ctx.strokeStyle = 'rgba(120,96,66,.45)'; ctx.lineWidth = 1;
      for (var kk = 1; kk <= 3; kk++) {
        ctx.beginPath(); ctx.moveTo(ox + st.x0 * c + 2, yy0 + kk * c / 4);
        ctx.lineTo(ox + (st.x1 + 1) * c - 2, yy0 + kk * c / 4); ctx.stroke();
      }
      ctx.restore();
    }

    windowSpecs(f).forEach(function (w) {
      if (w.side === 'left') {
        var ya = top + w.x0 * c, yb = top + (w.x1 + 1) * c;
        px(ox - 10, ya + 3, Math.max(7, Math.round(c * 0.22)), yb - ya - 6, '#B98C5E');
        px(ox - 9, ya + 4, Math.max(5, Math.round(c * 0.18)), yb - ya - 8, '#DCEFF7');
        px(ox - 10, ya + 3 + (yb - ya) / 2 - 1, Math.max(7, Math.round(c * 0.22)), 2, '#B98C5E');
      } else {
        var xa = ox + w.x0 * c, xb = ox + (w.x1 + 1) * c;
        var wh = Math.max(8, Math.round(c * 0.55)), wy = top + 3;
        px(xa + 3, wy, xb - xa - 6, wh, '#B98C5E');
        px(xa + 4, wy + 1, xb - xa - 8, wh - 2, '#DCEFF7');
        var mid = xa + (xb - xa) / 2;
        px(mid - 1, wy, 2, wh, '#B98C5E');
        px(xa + 3, wy + Math.round(wh * 0.55), xb - xa - 6, 1, 'rgba(120,150,165,.55)');
      }
    });

    var d = doorSpec();
    if (d && d.floor === f) {
      px(ox + d.x * c + 2, top + (d.y + 1) * c - 4, c - 4, 6, '#A97948');
      px(ox + d.x * c + 6, top + (d.y + 1) * c - 2, c - 12, 3, '#E2B98A');
    }

    var list = (snap.furniture || []).filter(function (it) { return it.floor === f; });
    list.sort(function (a, b) { return (a.y - b.y) || (a.x - b.x); });
    list.forEach(function (it) { drawFurniture(it, g); });

    ctx.save(); ctx.fillStyle = 'rgba(48,40,32,.84)';
    var tag = (f === 1 ? '1F' : '2F') + '  ' + (f === 1 ? '客厅 · 厨房 · 玄关' : '卧室 · 书房');
    ctx.font = '700 12px "Microsoft YaHei", sans-serif';
    var tw = ctx.measureText(tag).width + 20;
    roundRect(ox + 4, top + 3, tw, 17, 8); ctx.fill();
    ctx.fillStyle = 'rgba(255,252,244,.97)'; ctx.textAlign = 'left';
    ctx.fillText(tag, ox + 14, top + 16);
    ctx.restore();

    px(ox, top + ROWS * c - 3, COLS * c, 3, 'rgba(140,110,74,.16)');
    if (f === 2) {
      var sy = top + ROWS * c;
      var slabH = g.m ? g.m.slab : metrics(c).slab;
      px(ox - 8, sy + 3, COLS * c + 16, Math.max(5, slabH), '#B98C5E');
      px(ox - 8, sy + 3, COLS * c + 16, 3, '#D9AE7C');
      px(ox - 8, sy + 3 + Math.max(5, slabH) - 3, COLS * c + 16, 3, '#8A6134');
    }
  }

  function drawRoomWall(key, x, y, w, h) {
    var st = WALL[key] || ['#E9DCC4', 'planks'];
    px(x, y, w, h, st[0]);
    ctx.save(); ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();
    if (st[1] === 'planks') {
      for (var yy = y + 11; yy < y + h; yy += 11) px(x, yy, w, 1, 'rgba(140,116,82,.16)');
      for (var xx = x + 30; xx < x + w; xx += 60) px(xx, y, 1, h, 'rgba(140,116,82,.08)');
    } else if (st[1] === 'brick') {
      for (var by = y; by < y + h; by += 10) px(x, by, w, 1, 'rgba(150,116,74,.20)');
      var rowI = 0;
      for (var by2 = y; by2 < y + h; by2 += 10) {
        for (var bx = x + (rowI % 2 ? 0 : 16); bx < x + w; bx += 32) px(bx, by2, 1, 10, 'rgba(150,116,74,.16)');
        rowI++;
      }
    } else if (st[1] === 'tiles') {
      for (var ty = y; ty < y + h; ty += 18) px(x, ty, w, 1, 'rgba(150,140,120,.20)');
      for (var tx = x; tx < x + w; tx += 18) px(tx, y, 1, h, 'rgba(150,140,120,.20)');
    } else {
      for (var dy = y + 7; dy < y + h; dy += 13)
        for (var dx = x + 7; dx < x + w; dx += 13) px(dx, dy, 2, 2, 'rgba(150,138,176,.28)');
    }
    ctx.restore();
    ctx.strokeStyle = 'rgba(120,96,66,.20)'; ctx.lineWidth = 1;
    ctx.strokeRect(x + .5, y + .5, w - 1, h - 1);
  }
  function drawTag(txt, x, y) {
    txt = String(txt || '');
    ctx.save();
    ctx.font = '600 9px "Microsoft YaHei", sans-serif';
    var tw = ctx.measureText(txt).width + 12;
    ctx.fillStyle = 'rgba(56,46,36,.74)';
    roundRect(x + 3, y + 3, tw, 14, 6); ctx.fill();
    ctx.fillStyle = 'rgba(255,252,244,.95)'; ctx.textAlign = 'left';
    ctx.fillText(txt, x + 9, y + 13);
    ctx.restore();
  }

  function drawFurniture(it, g) {
    var c = g.c, ox = fox(), x = ox + it.x * c, y = fy(it.floor) + it.y * c;
    var iw = it.w || 1, ih = it.h || 1;
    var rows = furnArt(it.kind, iw, ih), pal = furnPalette(it.kind);
    var k = Math.max(1, Math.floor(c / 8));
    var imgW = iw * 8 * k, imgH = ih * 8 * k;
    var px0 = x + (iw * c - imgW) / 2, py0 = y + (ih * c - imgH) / 2;
    ctx.save(); ctx.fillStyle = 'rgba(92,72,48,.10)';
    roundRect(x + iw * c * 0.10, y + ih * c * 0.62, iw * c * 0.80, ih * c * 0.34, 4); ctx.fill();
    ctx.restore();
    for (var r = 0; r < rows.length; r++) {
      for (var ci = 0; ci < rows[r].length; ci++) {
        var col = pal[rows[r][ci]];
        if (!col) continue;
        px(px0 + ci * k, py0 + r * k, k, k, col);
      }
    }
    if (hoverId === it.id) {
      ctx.save(); ctx.strokeStyle = '#7FA37B'; ctx.lineWidth = 2;
      ctx.strokeRect(Math.round(x) + 1, Math.round(y) + 1, Math.round(iw * c) - 2, Math.round(ih * c) - 2);
      ctx.restore();
    }
    hit.furniture.push({ x: x, y: y, w: iw * c, h: ih * c, floor: it.floor, id: it.id, kind: it.kind });
  }

  function drawCharacter(c, floorGroup, posOverride) {
    var a = anim.get(c.id);
    if (!a || !a.visible) return;
    var g = hg(), c2 = g.c;
    if (c.doing === 'sleep' || (c.action && c.action.job === 'sleep')) { drawSleep(c, g); return; }
    var fl = floorGroup || c.floor || 1;
    var ox = fox(), top = fy(fl);
    var ax = posOverride ? posOverride.x : a.x, ay = posOverride ? posOverride.y : a.y;
    var pxp = ox + ax * c2, pyp = top + ay * c2;
    var k = Math.max(2, Math.round(c2 / 4.6));
    // 不让头顶跑出本层(跑到屋顶/楼上)
    var artH = HER_ART.length * k;
    var limTop = top + 4, limBot = top + ROWS * c2 - 2;
    if (pyp - artH < limTop) pyp = limTop + artH;
    if (pyp > limBot) pyp = limBot;
    var cols = roleCols(c);
    px(pxp - c2 * 0.28, pyp - c2 * 0.10, c2 * 0.56, c2 * 0.10, mixc('#F6EFE6', '#000000', 0.5));
    artDraw(HER_ART, cols, k, pxp - Math.floor(HER_ART[0].length * k / 2), pyp - HER_ART.length * k);
    var ck = Math.max(1, Math.round(c2 / 12));
    cursorImg(cols, ck, pxp, pyp - HER_ART.length * k - 4);
    if (c.id !== 'jingyu' && c2 >= 16) {
      ctx.save(); ctx.fillStyle = cols['5']; ctx.font = '9px "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'center'; ctx.fillText(c.name || '', pxp, pyp - HER_ART.length * k - CUR_ART.length * ck - 8);
      ctx.restore();
    }
    var hw = Math.max(c2 * 0.6, Math.floor(HER_ART[0].length * k / 2));
    hit.chars.push({ x: pxp - hw, y: pyp - HER_ART.length * k - 18, w: hw * 2, h: HER_ART.length * k + 20, id: c.id, char: c });
  }

  function drawSleep(c, g) {
    var c2 = g.c, fl = c.floor === 2 ? 2 : (c.floor || 1);
    var ox = fox(), top = fy(fl);
    var bed = (snap.furniture || []).find(function (f) { return f.kind === 'bed' && f.floor === fl; });
    var bx = bed ? ox + (bed.x + 0.5) * c2 : ox + 2 * c2;
    var by = bed ? top + bed.y * c2 + c2 * 0.3 : top + c2 * 0.6;
    ctx.save();
    ctx.fillStyle = 'rgba(240,235,225,.92)';
    roundRect(bx - c2 * 0.9, by, c2 * 1.8, c2 * 0.9, 7); ctx.fill();
    ctx.fillStyle = 'rgba(196,216,232,.9)';
    roundRect(bx - c2 * 0.9, by + c2 * 0.35, c2 * 1.8, c2 * 0.55, 7); ctx.fill();
    var cols = roleCols(c);
    artDraw(HER_ART, cols, Math.max(1, Math.round(c2 / 7)), bx - 14, by - c2 * 0.2);
    ctx.fillStyle = 'rgba(90,110,130,.75)'; ctx.font = '10px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center'; ctx.fillText('z z', bx + 18, by - 6);
    ctx.restore();
    hit.chars.push({ x: bx - c2, y: by - c2 * 0.4, w: c2 * 2, h: c2 * 1.4, id: c.id, char: c });
  }

  // ---------------- 田地 ----------------
  function drawFarm() {
    var g = hg();
    var list = (snap.characters || []).filter(function (c) { return c.hired && (c.place || 'house') === 'farm'; });
    var plots = snap.plots || [];
    var floors = [1, 2].filter(function (f) { return plots.some(function (p) { return p.floor === f; }); });
    if (!floors.length) floors = [1, 2];
    // 先按引擎楼层分;若某层有地却没人,从人多的层调一个过去(只影响田地页显示)
    var assign = {};
    list.forEach(function (c) { var f = c.floor || 1; assign[c.id] = floors.indexOf(f) >= 0 ? f : floors[0]; });
    floors.forEach(function (f) {
      var here = list.filter(function (c) { return assign[c.id] === f; });
      if (here.length) return;
      var donorFloor = floors.filter(function (g2) {
        return g2 !== f && list.filter(function (c) { return assign[c.id] === g2; }).length > 1;
      })[0];
      if (donorFloor == null) return;
      var donor = list.filter(function (c) { return assign[c.id] === donorFloor; }).pop();
      if (donor) assign[donor.id] = f;
    });
    function plotPos(f, c) {
      var tid = c.action && c.action.target_id;
      var p = plots.filter(function (x) { return x.floor === f && x.id === tid; })[0] ||
              plots.filter(function (x) { return x.floor === f && x.crop; })[0] ||
              plots.filter(function (x) { return x.floor === f; })[0];
      return p ? { x: p.x + 0.5, y: p.y + 0.5 } : null;
    }
    [2, 1].forEach(function (f) {
      drawFarmFloor(g, f);
      ctx.save();
      ctx.beginPath();
      ctx.rect(fox() - 4, fy(f) - 2, COLS * g.c + 8, ROWS * g.c + 4);
      ctx.clip();
      list.filter(function (c) { return assign[c.id] === f; }).forEach(function (c) {
        var pos = (c.floor || 1) === f ? null : plotPos(f, c);
        drawCharacter(c, f, pos);
      });
      ctx.restore();
    });
  }
  function drawFarmFloor(g, f) {
    var c = g.c, ox = fox(), top = fy(f), Wc = COLS * c, Hc = ROWS * c;
    var frame = f === 2 ? '#5C7A5E' : '#A9814F';
    ctx.save();
    ctx.shadowColor = 'rgba(70,60,40,.16)'; ctx.shadowBlur = 12; ctx.shadowOffsetY = 4;
    roundRect(ox - 11, top - 11, Wc + 22, Hc + 22, 10);
    ctx.fillStyle = frame; ctx.fill();
    ctx.restore();
    roundRect(ox - 11, top - 11, Wc + 22, Hc + 22, 10);
    ctx.strokeStyle = 'rgba(60,50,34,.35)'; ctx.lineWidth = 1.6; ctx.stroke();
    px(ox - 11, top - 11, Wc + 22, 2, 'rgba(255,255,240,.22)');

    // 翻好的土
    px(ox, top, Wc, Hc, f === 2 ? '#7C5638' : '#8A6244');
    for (var fx = ox; fx < ox + Wc; fx += Math.max(6, c / 5))
      px(fx, top, 1.6, Hc, 'rgba(52,32,18,.13)');

    // 地块格子
    for (var yy = 0; yy < ROWS; yy++) {
      for (var xx = 0; xx < COLS; xx++) {
        var x0 = ox + xx * c, y0 = top + yy * c;
        px(x0 + 1, y0 + 1, c - 2, 1.6, 'rgba(126,164,104,.55)');
        px(x0 + 1, y0 + c - 2, c - 2, 1.6, 'rgba(52,72,42,.55)');
        px(x0 + 1, y0 + 1, 1.6, c - 2, 'rgba(126,164,104,.45)');
        px(x0 + c - 2, y0 + 1, 1.6, c - 2, 'rgba(52,72,42,.5)');
      }
    }

    var st = stairSpec();
    (snap.plots || []).filter(function (pl) { return pl.floor === f; }).forEach(function (pl) {
      if (pl.x >= st.x0 && pl.x <= st.x1 && pl.y >= st.y0 && pl.y <= st.y1) return;
      var x = ox + pl.x * c, y = top + pl.y * c, w = c, h = c;
      var dry = pl.dry || !pl.watered_at;
      px(x + 2, y + 2, w - 4, h - 4, dry ? '#8C7250' : '#6B4529');
      px(x + 2, y + 2, w - 4, 2, dry ? '#A08663' : '#875B39');
      if (pl.crop) {
        var meta = CROPS[pl.crop] || ['作物', '#8FBF86'];
        var prog = pl.progress == null ? 1 : pl.progress;
        var stage = pl.mature ? 3 : (prog < .3 ? 1 : 2);
        var cx = x + w / 2, base = y + h - 4;
        if (stage === 1) {
          px(cx - 1, base - 5, 2, 5, '#5C8A4E'); ell(cx - 3, base - 6, 3, 2, '#79AC63'); ell(cx + 3, base - 6, 3, 2, '#8CBE72');
        } else if (stage === 2) {
          px(cx - 1, base - 12, 2.4, 12, '#5C8A4E');
          ell(cx - 5, base - 10, 4, 3, '#6E9C56'); ell(cx + 5, base - 11, 4, 3, '#7FB069');
          ell(cx - 4, base - 14, 3.4, 2.6, '#8CBE72'); ell(cx + 4, base - 15, 3.4, 2.6, '#6E9C56');
        } else {
          px(cx - 1.2, base - 18, 2.6, 18, '#4F7C45');
          ell(cx - 6, base - 14, 5, 3.6, '#5F8F4C'); ell(cx + 6, base - 15, 5, 3.6, '#6E9C56');
          ell(cx, base - 20, 4.6, 4, meta[1]);
          ell(cx - 5, base - 20, 3.6, 3.2, meta[1]); ell(cx + 5, base - 19, 3.6, 3.2, meta[1]);
        }
      }
      hit.plots.push({ x: x, y: y, w: w, h: h, floor: pl.floor, id: pl.id, plot: pl });
    });

    if (f === 2) {
      // 玻璃温室
      var grd = ctx.createLinearGradient(0, top - 11, 0, top + Hc);
      grd.addColorStop(0, 'rgba(224,246,242,.26)');
      grd.addColorStop(.55, 'rgba(224,246,242,.07)');
      grd.addColorStop(1, 'rgba(224,246,242,.02)');
      ctx.fillStyle = grd; roundRect(ox - 11, top - 11, Wc + 22, Hc + 22, 10); ctx.fill();
      ctx.strokeStyle = 'rgba(58,86,68,.55)'; ctx.lineWidth = 2;
      for (var gx = ox; gx <= ox + Wc + 1; gx += c * 2) { ctx.beginPath(); ctx.moveTo(gx, top - 11); ctx.lineTo(gx, top + Hc + 11); ctx.stroke(); }
      ctx.beginPath(); ctx.moveTo(ox - 11, top + Hc / 2); ctx.lineTo(ox + Wc + 11, top + Hc / 2); ctx.stroke();
      ctx.strokeStyle = 'rgba(255,255,255,.18)'; ctx.lineWidth = 1;
      for (var rx = ox - 11; rx < ox + Wc; rx += c * 2) {
        ctx.beginPath(); ctx.moveTo(rx + 4, top + Hc + 8); ctx.lineTo(rx + c * 1.2, top - 8); ctx.stroke();
      }
      // 拱形玻璃顶
      var archH = 44;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(ox - 11, top - 11);
      ctx.quadraticCurveTo(ox + Wc / 2, top - 11 - archH * 2, ox + Wc + 11, top - 11);
      ctx.closePath();
      var ag = ctx.createLinearGradient(0, top - 11 - archH, 0, top - 11);
      ag.addColorStop(0, 'rgba(196,230,226,.55)'); ag.addColorStop(1, 'rgba(214,240,236,.16)');
      ctx.fillStyle = ag; ctx.fill();
      ctx.strokeStyle = 'rgba(58,86,68,.75)'; ctx.lineWidth = 2.4; ctx.stroke();
      ctx.clip();
      ctx.strokeStyle = 'rgba(58,86,68,.42)'; ctx.lineWidth = 1.4;
      for (var ax = ox; ax <= ox + Wc; ax += c * 2) { ctx.beginPath(); ctx.moveTo(ax, top - 11 - archH); ctx.lineTo(ax, top - 11); ctx.stroke(); }
      ctx.restore();
    }
    if (f === 1) {
      for (var px2 = ox + 6; px2 < ox + Wc - 4; px2 += 26) {
        px(px2, top + Hc + 4, 5, 12, '#B98C5E'); px(px2, top + Hc + 4, 5, 3, '#D9AE7C');
        px(px2, top + Hc + 10, 5, 2, '#8A6134');
      }
      px(ox - 6, top + Hc + 13, Wc + 12, 4, '#A97948');
      px(ox - 6, top + Hc + 13, Wc + 12, 1.6, '#C79A6B');
      px(ox - 6, top, 5, Hc, '#B98C5E'); px(ox + Wc + 1, top, 5, Hc, '#B98C5E');
    }

    ctx.save(); ctx.fillStyle = 'rgba(48,40,32,.84)';
    var tag = (f === 1 ? '1F' : '2F') + '  ' + (f === 1 ? '大田 · 种菜' : '温室 · 种花');
    ctx.font = '700 12px "Microsoft YaHei", sans-serif';
    var tw = ctx.measureText(tag).width + 20;
    roundRect(ox + 4, top + 4, tw, 17, 8); ctx.fill();
    ctx.fillStyle = 'rgba(255,252,244,.97)'; ctx.textAlign = 'left';
    ctx.fillText(tag, ox + 14, top + 17);
    ctx.restore();

    if (f === 2) {
      var sy = top + Hc;
      px(ox - 11, sy + 4, Wc + 22, (g.m ? g.m.slab : metrics(c).slab), '#8FA37E');
      px(ox - 11, sy + 4, Wc + 22, 3, '#A9BC96');
    }
  }

  // ---------------- 悬停/选中 ----------------
  function drawHover() {
    if (!hoverCell) return;
    var g = hg(), c = g.c;
    var x = fox() + hoverCell.cx * c, y = fy(hoverCell.floor) + hoverCell.cy * c;
    ctx.save();
    ctx.fillStyle = 'rgba(255,253,247,.12)'; ctx.fillRect(x, y, c, c);
    ctx.strokeStyle = 'rgba(127,163,123,.9)'; ctx.lineWidth = 1.5;
    ctx.strokeRect(x + 1.5, y + 1.5, c - 3, c - 3);
    ctx.restore();
  }
  function drawSelection() {
    var r = selected.rect;
    ctx.save(); ctx.globalAlpha = .95; ctx.strokeStyle = '#E8BB66'; ctx.lineWidth = 2.5;
    ctx.setLineDash([5, 3]);
    ctx.strokeRect(Math.round(r.x) + 1, Math.round(r.y) + 1, r.w - 2, r.h - 2);
    ctx.restore();
  }
  function drawOffline(msg) {
    if (!ctx) return;
    px(0, 0, W, H, '#EFE4CE');
    ctx.save(); ctx.fillStyle = '#5B4A3A'; ctx.textAlign = 'center';
    ctx.font = '600 18px "Microsoft YaHei", sans-serif'; ctx.fillText('小窝引擎没有响应', W / 2, H / 2 - 16);
    ctx.font = '12px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#8B7A64';
    ctx.fillText(String(msg || '').slice(0, 90), W / 2, H / 2 + 14);
    ctx.restore();
  }

  // ---------------- 状态卡 / 右栏 ----------------
  function updateHud() {
    var s = snap || {}, her = s.her || {};
    var now = $('fishNow');
    if (now) now.textContent = her.text ? (NAMES[her.id] || '鲸鱼娘') + ' ' + her.text : '读取中…';
    var coins = $('fishCoins');
    if (coins) coins.textContent = s.coins == null ? '—' : s.coins;
    var bars = $('fishBars');
    if (bars) {
      var me = (s.characters || []).find(function (c) { return c.id === 'jingyu'; }) || {};
      var items = [['能量', me.energy == null ? 0 : me.energy]];
      if (her.mood != null) items.push(['心情', her.mood]);
      if (her.full != null) items.push(['饱腹', her.full]);
      if (her.clean != null) items.push(['清洁', her.clean]);
      var html = items.map(function (it) {
        var n = Math.round((it[1] || 0) / 10);
        var dots = '';
        for (var i = 0; i < 10; i++) dots += '<b class="' + (i < n ? (i < 4 ? 'mid' : 'on') : '') + '"></b>';
        return '<div class="fsBar"><span>' + it[0] + '</span><i>' + dots + '</i><span>' + Math.round(it[1]) + '</span></div>';
      }).join('');
      var ph = her.phase || s.phase || '';
      if (ph) html += '<span class="fsPhase">' + esc(ph) + '</span>';
      bars.innerHTML = html;
    }
    var mem = $('fishMem');
    if (mem) {
      var m = s.memory || {};
      mem.textContent = m.last_err ? ('记忆待同步 ' + (m.queue || 0)) : '记忆已同步';
      mem.title = m.last_err || ('队列 ' + (m.queue || 0));
    }
    var where = $('fishChipWhere'), cnt = $('fishChipCount'), title = $('fishMainTitle');
    if (where) where.textContent = (her.place === 'farm' ? '她在田地' : '她在家') + (her.floor ? (' · ' + her.floor + 'F') : '');
    if (cnt) cnt.textContent = ((s.furniture || []).length) + ' 件家具';
    if (title) title.textContent = view === 'farm' ? '两层田地' : '两层小楼';
  }

  var currentSideMarkup = '';

  function updateSideContent(box, markup) {
    if (markup === currentSideMarkup) return false;
    var scrollTop = box.scrollTop;
    box.innerHTML = markup;
    box.scrollTop = scrollTop;
    currentSideMarkup = markup;
    return true;
  }

  function recentEvents(source) {
    return (snap.events || []).filter(function (event) {
      return event.source === source && event.text;
    }).slice(0, 6);
  }

  function renderSide() {
    var box = $('fishSide'); if (!box || !snap) return;
    if (view === 'farm') {
      var plots = snap.plots || [];
      var ripe = plots.filter(function (p) { return p.mature; }).length;
      var dry = plots.filter(function (p) { return p.dry || !p.watered_at; }).length;
      var wh = snap.warehouse || {};
      var keys = Object.keys(wh).filter(function (k) { return wh[k] > 0; });
      var hist = recentEvents('farm');
      var farmMarkup =
        '<div class="glass card"><h2>农活</h2><div class="fishShop" style="grid-template-columns:repeat(2,1fr)">' +
        '<div class="fishShopItem"><b>' + ripe + '</b><span>熟了</span></div>' +
        '<div class="fishShopItem"><b>' + dry + '</b><span>干了</span></div>' +
        '<div class="fishShopItem"><b>' + plots.filter(function (p) { return p.crop; }).length + '</b><span>在种</span></div>' +
        '<div class="fishShopItem"><b>' + plots.length + '</b><span>总地块</span></div></div>' +
        '<div class="fishBulk">' +
        '<button type="button" class="fishBulkBtn" data-bulk="harvest"' + (ripe ? '' : ' disabled') + '>收全部熟的</button>' +
        '<button type="button" class="fishBulkBtn ghost" data-bulk="water"' + (dry ? '' : ' disabled') + '>一键浇水</button>' +
        '</div></div>' +
        '<div class="glass card"><h2>仓库</h2>' +
        (keys.length ? '<div class="fishShop">' + keys.map(function (k) {
          return '<div class="fishShopItem"><b>' + esc(k) + '</b><span>×' + wh[k] + '</span></div>';
        }).join('') + '</div>' : '<p class="hint" style="margin:0">空着,收下来的东西放这里。</p>') + '</div>' +
        '<div class="glass card"><h2>地里的动静</h2><div class="fishLog">' +
        (hist.length ? hist.map(function (w) {
          return '<div class="fishLogRow"><span>' + esc(String(w.at || '').slice(5, 16)) + '</span><div>' + esc(w.text || '') + '</div></div>';
        }).join('') : '<p class="hint" style="margin:0">还没有动静。</p>') + '</div></div>';
      if (!updateSideContent(box, farmMarkup)) return;
      var bulks = box.querySelectorAll('[data-bulk]');
      for (var bi = 0; bi < bulks.length; bi++) {
        bulks[bi].addEventListener('click', function () { bulkFarm(this.getAttribute('data-bulk')); });
      }
      return;
    }
    var cat = catalogOf(), kinds = Object.keys(cat);
    var wh2 = snap.warehouse || {};
    var keys2 = Object.keys(wh2).filter(function (k) { return wh2[k] > 0; });
    var hist2 = recentEvents('home');
    var it2 = snap.items || {};
    var keys3 = Object.keys(it2).filter(function (k) { return it2[k] > 0; });
    var seeds = snap.seeds || {};
    var seedKeys = Object.keys(seeds).filter(function (k) { return seeds[k] > 0; });
    var houseMarkup =
      '<div class="glass card"><h2>背包</h2>' +
      ((keys3.length || keys2.length || seedKeys.length) ? '<div class="fishShop">' +
        keys3.map(function (k) {
          return '<div class="fishShopItem"><b>' + esc(k) + '</b><span>×' + it2[k] + '</span></div>';
        }).join('') +
        seedKeys.map(function (k) {
          var name = (CROPS[k] || [k])[0] + '种子';
          return '<div class="fishShopItem"><b>' + esc(name) + '</b><span>×' + seeds[k] + '</span></div>';
        }).join('') +
        keys2.map(function (k) {
          return '<div class="fishShopItem"><b>' + esc(k) + '</b><span>×' + wh2[k] + '</span></div>';
        }).join('') + '</div>'
        : '<p class="hint" style="margin:0">空的。采到的东西和收的菜都会放这里。</p>') + '</div>' +
      '<div class="glass card"><h2>家具店</h2><div class="fishShop">' +
      kinds.map(function (k) {
        var it = cat[k] || {};
        return '<div class="fishShopItem" data-buy="' + esc(k) + '"><b>' + esc(it.name || k) + '</b>' +
          '<span>' + (it.w || 1) + '×' + (it.h || 1) + '</span><i>' + (it.price || 0) + ' 金币</i></div>';
      }).join('') + '</div><p class="hint" style="margin:6px 0 0">点一件,再点地上的空地放下。</p></div>' +
      '<div class="glass card"><h2>家里的动静</h2><div class="fishLog">' +
      (hist2.length ? hist2.map(function (w) {
        return '<div class="fishLogRow"><span>' + esc(String(w.at || '').slice(5, 16)) + '</span><div>' + esc(w.text || '') + '</div></div>';
      }).join('') : '<p class="hint" style="margin:0">还没有动静。</p>') + '</div></div>';
    if (!updateSideContent(box, houseMarkup)) return;
    var buys = box.querySelectorAll('[data-buy]');
    for (var i = 0; i < buys.length; i++) {
      buys[i].addEventListener('click', function () {
        var k = this.getAttribute('data-buy');
        pendingBuy = k;
        toast('先在左边点一块空地,再放「' + ((cat[k] || {}).name || k) + '」');
      });
    }
  }

  function bulkFarm(kind) {
    var list = (snap && snap.plots || []).filter(function (p) {
      return kind === 'harvest' ? p.mature : (p.dry || !p.watered_at);
    });
    if (!list.length) { toast(kind === 'harvest' ? '还没有熟的呢' : '地里都不干'); return; }
    var i = 0, done = 0, fail = 0;
    if (busyEl) busyEl.textContent = (kind === 'harvest' ? '收菜中…' : '浇水中…');
    function step() {
      if (i >= list.length) {
        if (busyEl) busyEl.textContent = '';
        toast((kind === 'harvest' ? '收了 ' : '浇了 ') + done + ' 块' + (fail ? (' · 失败 ' + fail) : ''));
        return refresh(true);
      }
      var p = list[i++];
      var body = kind === 'harvest' ? { type: 'harvest', plot: p.id } : { type: 'water', plot: p.id };
      invoke('home_action', { body: body }).then(function (r) {
        if (r && r.ok === false) fail++; else done++;
        step();
      }).catch(function () { fail++; step(); });
    }
    step();
  }

  // ---------------- 弹层 ----------------
  function setPop(html) { if (!pop) return; pop.innerHTML = html; pop.hidden = false; bindPop(); }
  function hidePop() { if (pop) pop.hidden = true; selected = null; pendingBuy = null; render(); }
  function bindPop() {
    if (!pop) return;
    var x = pop.querySelector('[data-close]');
    if (x) x.addEventListener('click', hidePop);
    var acts = pop.querySelectorAll('[data-act]');
    for (var i = 0; i < acts.length; i++) {
      acts[i].addEventListener('click', function () {
        var a = this.getAttribute('data-act');
        if (a === 'plant') { var sel = $('fishCropSel'); act({ type: 'plant', plot: selected.id, crop: sel ? sel.value : 'tomato' }); hidePop(); }
        else if (a === 'buy-seed') { var s2 = $('fishCropSel'); act({ type: 'buy_seed', crop: s2 ? s2.value : 'tomato', qty: 1 }); }
        else if (a === 'water' || a === 'fertilize' || a === 'harvest') { act({ type: a, plot: selected.id }); hidePop(); }
        else if (a === 'assign') { var job = $('fishJobSel'); if (selected && job) act({ type: 'assign_work', id: selected.id, job: job.value }); }
        else if (a === 'cancel-job') { if (selected) act({ type: 'cancel_work', id: selected.id }); }
        else if (a === 'fire') { if (selected) act({ type: 'fire', id: selected.id }); hidePop(); }
        else if (a === 'buy-furn') {
          var fs = $('fishFurnSel');
          if (fs && selected && selected.type === 'cell') act({ type: 'buy_furniture', kind: fs.value, floor: selected.floor, x: selected.cx, y: selected.cy });
          hidePop();
        }
      });
    }
  }

  function openFurniture(h) {
    var it = (snap.furniture || []).find(function (x) { return x.id === h.id; });
    if (!it) return;
    selected = { type: 'furniture', id: it.id, rect: h };
    var cat = catalogOf()[it.kind] || {};
    setPop('<div class="popHead"><b>' + esc(cat.name || it.name || it.kind) + '</b><button class="popX" data-close>×</button></div>' +
      '<div class="popTag">' + esc(cat.type || '家具') + ' · ' + (it.w || 1) + '×' + (it.h || 1) + ' 格 · ' + (it.floor === 2 ? '2F' : '1F') + '</div>' +
      '<p class="popDesc">' + esc(cat.desc || '') + '</p>' +
      '<div class="popRow"><span>用途</span><b>' + esc(cat.usage || '') + '</b></div>' +
      '<div class="popRow"><span>用了几次</span><b>' + (it.used_count || 0) + ' 次</b></div>');
    render();
  }
  function openChar(h) {
    var c = (snap.characters || []).find(function (x) { return x.id === h.id; });
    if (!c) return;
    selected = { type: 'char', id: c.id, rect: h };
    var jobOpts = Object.keys(JOB_CN).filter(function (k) { return k; }).map(function (k) {
      return '<option value="' + k + '"' + (c.job === k ? ' selected' : '') + '>' + JOB_CN[k] + '</option>';
    }).join('');
    setPop('<div class="popHead"><b>' + esc(c.name || c.id) + '</b><button class="popX" data-close>×</button></div>' +
      '<div class="popTag">' + (c.place === 'farm' ? '在田地' : '在屋里') + ' · ' + (c.floor ? c.floor + 'F' : '') + ' · ' + esc(JOB_CN[c.job] || c.job || '空闲') + '</div>' +
      '<div class="popRow"><span>体力</span><b>' + (c.energy == null ? '—' : c.energy) + ' / 100</b></div>' +
      '<div class="popBar"><i style="width:' + Math.max(2, c.energy || 0) + '%"></i></div>' +
      (c.hired ? '<label class="popLabel">工作安排</label><select id="fishJobSel">' + jobOpts + '</select>' +
        '<div class="popActs"><button class="mini" data-act="assign">保存工作</button>' +
        '<button class="mini ghostBtn" data-act="cancel-job">取消派活</button></div>' : '') +
      (c.hired && c.id !== 'jingyu' ? '<div class="popActs"><button class="mini dangerBtn" data-act="fire">解雇</button></div>' : ''));
    render();
  }
  function openPlot(h) {
    var pl = (snap.plots || []).find(function (x) { return x.id === h.id; });
    if (!pl) return;
    selected = { type: 'plot', id: pl.id, rect: h };
    var meta = CROPS[pl.crop] || ['作物'];
    var pct = Math.round((pl.progress || 0) * 100);
    var when = pl.mature ? '熟了,可以收' : ('还要 ' + (pl.left_minutes == null ? '?' : Math.max(1, pl.left_minutes)) + ' 分钟');
    if (!pl.mature && pl.dry) when += ' · 土是干的,长得慢';
    var opts = Object.keys(CROPS).map(function (k) { return '<option value="' + k + '">' + CROPS[k][0] + '</option>'; }).join('');
    var html = '<div class="popHead"><b>地块 ' + esc(pl.id) + '</b><button class="popX" data-close>×</button></div>' +
      '<div class="popTag">' + (pl.floor === 2 ? '2F 温室' : '1F 大田') + ' · 第 ' + pl.x + ',' + pl.y + ' 块 · 长了 ' + pct + '%</div>';
    if (!pl.crop) {
      html += '<p class="popDesc">这块地空着,可以播种。</p><label class="popLabel">种什么</label><select id="fishCropSel">' + opts + '</select>' +
        '<div class="popActs"><button class="mini" data-act="plant">播种(3 金币)</button>' +
        '<button class="mini ghostBtn" data-act="buy-seed">买 1 份种子(2 金币)</button></div>';
    } else {
      html += '<div class="popRow"><span>作物</span><b>' + esc(meta[0]) + '</b></div>' +
        '<div class="popRow"><span>状态</span><b>' + esc(when) + '</b></div>' +
        '<div class="popBar"><i style="width:' + Math.max(3, Math.min(100, pct)) + '%"></i></div>' +
        '<div class="popActs"><button class="mini" data-act="water">浇水</button>' +
        '<button class="mini ghostBtn" data-act="fertilize">施肥(2 金币)</button>' +
        '<button class="mini" data-act="harvest"' + (pl.mature ? '' : ' disabled') + '>收获</button></div>';
    }
    setPop(html);
    render();
  }
  function openCell(floor, cx, cy) {
    var g = hg(), c = g.c;
    selected = { type: 'cell', floor: floor, cx: cx, cy: cy, rect: { x: fox() + cx * c, y: fy(floor) + cy * c, w: c, h: c } };
    var cat = catalogOf();
    var opts = Object.keys(cat).map(function (k) {
      var it = cat[k] || {};
      return '<option value="' + k + '">' + esc(it.name || k) + '(' + (it.price || 0) + ' 金币)</option>';
    }).join('');
    setPop('<div class="popHead"><b>空地 ' + (floor === 2 ? '2F' : '1F') + ' · ' + cx + ',' + cy + '</b><button class="popX" data-close>×</button></div>' +
      '<p class="popDesc">这块地空着,可以放一件家具。</p>' +
      '<label class="popLabel">放什么</label><select id="fishFurnSel">' + opts + '</select>' +
      '<div class="popActs"><button class="mini" data-act="buy-furn">买下来放这儿</button></div>');
    render();
  }

  // ---------------- 交互 ----------------
  function hitTest(mx, my) {
    var i, h, list = [hit.chars, hit.plots, hit.furniture];
    for (var l = 0; l < list.length; l++) {
      for (i = 0; i < list[l].length; i++) {
        h = list[l][i];
        if (mx >= h.x && mx <= h.x + h.w && my >= h.y && my <= h.y + h.h) return h;
      }
    }
    return null;
  }
  function cellAt(mx, my) {
    var g = hg(), c = g.c;
    for (var f = 1; f <= 2; f++) {
      var ox = fox(), top = fy(f);
      if (mx >= ox && mx <= ox + COLS * c && my >= top && my <= top + ROWS * c) {
        return { floor: f, cx: Math.floor((mx - ox) / c), cy: Math.floor((my - top) / c) };
      }
    }
    return null;
  }
  function pos(e) {
    var r = cv.getBoundingClientRect();
    return { x: (e.clientX - r.left) * (W / r.width), y: (e.clientY - r.top) * (H / r.height) };
  }
  function onMove(e) {
    var p = pos(e), h = hitTest(p.x, p.y);
    var newHover = h && h.id ? h.id : null;
    var cell = (!h && view === 'house') ? cellAt(p.x, p.y) : null;
    var changed = newHover !== hoverId || (!!cell) !== (!!hoverCell) ||
      (cell && hoverCell && (cell.floor !== hoverCell.floor || cell.cx !== hoverCell.cx || cell.cy !== hoverCell.cy));
    hoverId = newHover; hoverCell = cell;
    cv.style.cursor = (h || cell) ? 'pointer' : 'default';
    if (changed) render();
  }
  function onClick(e) {
    var p = pos(e), h = hitTest(p.x, p.y);
    if (h) {
      if (h.plot) { openPlot(h); return; }
      if (h.char) { openChar(h); return; }
      if (h.kind) { openFurniture(h); return; }
      return;
    }
    if (view === 'house') {
      var cell = cellAt(p.x, p.y);
      if (cell) {
        if (pendingBuy) { act({ type: 'buy_furniture', kind: pendingBuy, floor: cell.floor, x: cell.cx, y: cell.cy }); pendingBuy = null; }
        else openCell(cell.floor, cell.cx, cell.cy);
        return;
      }
    }
    hidePop();
  }

  // ---------------- 头像 ----------------
  function drawAvatar() {
    var cvs = $('fishAva'); if (!cvs) return;
    var g2 = cvs.getContext('2d');
    g2.clearRect(0, 0, 120, 120);
    var k = 8, cols = roleCols({ id: 'jingyu' });
    var wpx = HER_ART[0].length * k, hpx = HER_ART.length * k;
    var x0 = (120 - wpx) / 2, y0 = (120 - hpx) / 2 + 6;
    g2.fillStyle = 'rgba(120,160,200,.16)'; g2.beginPath(); g2.arc(60, 64, 44, 0, Math.PI * 2); g2.fill();
    for (var r = 0; r < HER_ART.length; r++) {
      for (var ci = 0; ci < HER_ART[r].length; ci++) {
        var col = cols[HER_ART[r][ci]];
        if (!col) continue;
        g2.fillStyle = col; g2.fillRect(x0 + ci * k, y0 + r * k, k, k);
      }
    }
  }

  // ---------------- 视图切换 ----------------
  function setView(v) {
    var changed = view !== v;
    view = v;
    var tabs = $('fishTabs');
    if (tabs) {
      var bs = tabs.querySelectorAll('[data-fish-view]');
      for (var i = 0; i < bs.length; i++) bs[i].classList.toggle('on', bs[i].getAttribute('data-fish-view') === v);
    }
    var hp = $('fishHirePage'), dp = $('fishDiaryPage');
    var wp = $('fishWorkshopPage'), lp = $('fishLedgerPage');
    if (hp) hp.hidden = v !== 'hire';
    if (dp) dp.hidden = v !== 'diary';
    if (wp) wp.hidden = v !== 'workshop';
    if (lp) lp.hidden = v !== 'ledger';
    var full = (v === 'hire' || v === 'diary' || v === 'workshop' || v === 'ledger');
    var main = document.querySelector('#p-fish .fishMain');
    if (main) main.style.display = full ? 'none' : '';
    var side = $('fishSide');
    if (side && side.parentNode) side.parentNode.style.display = full ? 'none' : '';
    if (v === 'house' || v === 'farm') fitCanvas();
    if (v === 'hire') renderHirePage();
    if (v === 'diary') renderDiaryPage();
    renderExtraPage();
    updateHud();
    renderSide();
    if (changed && side) side.scrollTop = 0;
    render();
  }

  function renderExtraPage() {
    var W = window.FishWorkshop;
    if (!W || !snap) return;
    if (view === 'workshop' && W.renderWorkshop) W.renderWorkshop($('fishWorkshopPage'), snap);
    if (view === 'ledger' && W.renderLedger) W.renderLedger($('fishLedgerPage'), snap);
  }

  function portrait(sel, id) {
    var cvs = sel.querySelector('canvas[data-look]');
    if (!cvs) return;
    var g2 = cvs.getContext('2d');
    var W0 = cvs.width, H0 = cvs.height;
    g2.clearRect(0, 0, W0, H0);
    var k = Math.max(2, Math.floor(Math.min(W0 / HER_ART[0].length, H0 / HER_ART.length)));
    var cols = roleCols({ id: id });
    var wpx = HER_ART[0].length * k, hpx = HER_ART.length * k;
    var x0 = Math.round((W0 - wpx) / 2), y0 = Math.round((H0 - hpx) / 2);
    for (var r = 0; r < HER_ART.length; r++) {
      for (var ci = 0; ci < HER_ART[r].length; ci++) {
        var col = cols[HER_ART[r][ci]];
        if (!col) continue;
        g2.fillStyle = col;
        g2.fillRect(x0 + ci * k, y0 + r * k, k, k);
      }
    }
  }

  function renderHirePage() {
    var el = $('fishHirePage'); if (!el || !snap) return;
    var slots = snap.hire_slots || { house: 3, farm: 3 };
    var staff = (snap.characters || []).filter(function (c) { return c.hired && c.id !== 'jingyu'; });
    var avail = (snap.available_characters || []).filter(function (c) {
      return !staff.some(function (s2) { return s2.id === c.id; });
    });
    var houseN = staff.filter(function (c) { return (c.place || 'house') !== 'farm'; }).length;
    var farmN = staff.filter(function (c) { return c.place === 'farm'; }).length;
    var html = '<div class="glass card"><h2>雇佣处</h2>' +
      '<p class="hint">雇一个人 ' + (snap.hire_cost || 10) + ' 金币。田里的人会收菜、浇水;家里的人收拾屋子。钱不够发工资他们会停工,补发后复工。</p>' +
      '<div class="hireStats">' +
      '<div class="hireStat"><b>' + houseN + ' / ' + (slots.house == null ? 3 : slots.house) + '</b><span>家里名额</span></div>' +
      '<div class="hireStat"><b>' + farmN + ' / ' + (slots.farm == null ? 3 : slots.farm) + '</b><span>田地名额</span></div>' +
      '<div class="hireStat"><b>' + staff.length + '</b><span>已雇佣</span></div>' +
      '<div class="hireStat"><b>' + (snap.hire_cost || 10) + '</b><span>雇一个人</span></div>' +
      '</div></div>';

    html += '<div class="glass card"><h2>已雇佣 <span class="muted">' + staff.length + ' 人</span></h2>' +
      (staff.length ? '<div class="hireGrid">' + staff.map(function (c) {
        return '<div class="hireCard">' +
          '<div class="hireAva"><canvas data-look="' + esc(c.id) + '" width="96" height="96"></canvas></div>' +
          '<div class="hireInfo"><b>' + esc(c.name) + '</b>' +
          '<span>' + (c.place === 'farm' ? '在田地' : '在屋里') + ' · ' + esc(JOB_CN[c.job] || '空闲') + '</span>' +
          '<i class="hireBar"><b style="width:' + Math.max(0, Math.min(100, c.energy == null ? 0 : c.energy)) + '%"></b></i></div>' +
          '<button type="button" class="hireBtn ghost" data-fire="' + esc(c.id) + '">解雇</button>' +
          '</div>';
      }).join('') + '</div>' : '<p class="hint" style="margin:0">还没雇人。下面是能雇的人。</p>') + '</div>';

    html += '<div class="glass card"><h2>可雇佣 <span class="muted">' + avail.length + ' 人</span></h2>' +
      (avail.length ? '<div class="hireGrid">' + avail.map(function (c) {
        return '<div class="hireCard">' +
          '<div class="hireAva"><canvas data-look="' + esc(c.id) + '" width="96" height="96"></canvas></div>' +
          '<div class="hireInfo"><b>' + esc(c.name || NAMES[c.id] || c.id) + '</b>' +
          '<span>' + esc(c.desc || c.role || '') + '</span></div>' +
          '<button type="button" class="hireBtn" data-hire="' + esc(c.id) + '">雇 ' + (c.hire_cost || snap.hire_cost || 10) + ' 金币</button>' +
          '</div>';
      }).join('') + '</div>' : '<p class="hint" style="margin:0">都雇完了。</p>') + '</div>';

    el.innerHTML = html;
    var avas = el.querySelectorAll('.hireAva');
    for (var ai = 0; ai < avas.length; ai++) portrait(avas[ai], avas[ai].querySelector('canvas').getAttribute('data-look'));
    var hs = el.querySelectorAll('[data-hire]');
    for (var i = 0; i < hs.length; i++) {
      hs[i].addEventListener('click', function () { act({ type: 'hire', id: this.getAttribute('data-hire') }); });
    }
    var fs = el.querySelectorAll('[data-fire]');
    for (var fi = 0; fi < fs.length; fi++) {
      fs[fi].addEventListener('click', function () { act({ type: 'fire', id: this.getAttribute('data-fire') }); });
    }
  }

  function renderDiaryPage() {
    var el = $('fishDiaryPage'); if (!el || !snap) return;
    var NOISE = { work_start: 1, task_done: 1, walk: 1, idle: 1, rest: 1, furniture_move: 1, furniture_rotate: 1, move: 1 };
    var d = (snap.diary || []).filter(function (x) { return !NOISE[x.type]; });
    if (!d.length) {
      el.innerHTML = '<div class="glass card"><h2>生活日记</h2><p class="hint" style="margin:0">还没有值得记下来的事。</p></div>';
      return;
    }
    var groups = [], cur = null;
    d.slice(0, 120).forEach(function (x) {
      var at = String(x.at || '');
      var day = at.length >= 10 ? at.slice(5, 10) : (at.slice(0, 5) || '今天');
      if (!cur || cur.day !== day) { cur = { day: day, rows: [] }; groups.push(cur); }
      cur.rows.push(x);
    });
    el.innerHTML = '<div class="glass card"><h2>生活日记 <span class="muted">' + d.length + ' 条</span></h2>' +
      '<div class="diaryWrap">' + groups.map(function (g) {
        return '<div class="diaryGroup"><div class="diaryDay">' + esc(g.day) + '</div>' +
          g.rows.map(function (x) {
            return '<div class="diaryRow"><span class="diaryAt">' + esc(String(x.at || '').slice(11, 16)) + '</span>' +
              '<div class="diaryText">' + esc(x.text || x.content || '') + '</div></div>';
          }).join('') + '</div>';
      }).join('') + '</div></div>';
  }

  function fitCanvas() {
    if (!cv) return;
    var st = $('fishStage'); if (!st) return;
    var r = st.getBoundingClientRect();
    var aw = Math.max(300, Math.round(r.width) - 6), ah = Math.max(260, Math.round(r.height) - 6);
    var c = calcC(aw, ah), m = metrics(c);
    var w = Math.ceil(c * COLS + 48);
    // 画布高度必须 ≥ 屋顶 + 楼板 + 两层,1F 不能被裁掉
    var h = Math.ceil(m.total + PAD * 2 + 24);
    if (w === W && h === H && cv.style.width) return;
    W = w; H = h;
    cv.width = W; cv.height = H;
    cv.style.width = W + 'px';
    cv.style.height = H + 'px';
    render();
  }

  // ---------------- 挂载 ----------------
  function mount() {
    if (mounted) { refresh(true); return; }
    mounted = true;
    cv = $('fishCanvas');
    if (!cv) return;
    ctx = cv.getContext('2d');
    pop = $('fishPop'); toastEl = $('fishToast'); busyEl = $('fishBusy');
    var tabs = $('fishTabs');
    if (tabs) {
      tabs.addEventListener('click', function (e) {
        var b = e.target.closest ? e.target.closest('[data-fish-view]') : null;
        if (b) setView(b.getAttribute('data-fish-view'));
      });
    }
    fitCanvas();
    window.addEventListener('resize', function () { fitCanvas(); });
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(function () { fitCanvas(); });
      var stEl = $('fishStage');
      if (stEl) ro.observe(stEl);
    }
    cv.addEventListener('mousemove', onMove);
    cv.addEventListener('click', onClick);
    cv.addEventListener('mouseleave', function () { hoverId = null; hoverCell = null; render(); });
    refresh(true);
    pollTimer = setInterval(function () { refresh(false); }, POLL_MS);
    function loop(ts) {
      if (!rafLast) rafLast = ts;
      var dt = Math.min(MAX_DT, (ts - rafLast) / 1000);
      rafLast = ts;
      tick += dt;
      stepAnim(dt);
      render();
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
  }

  window.FishHome = {
    mount: mount, refresh: refresh, setView: setView, drawAvatar: drawAvatar,
    act: act,
    snapshot: function () { return snap; },
    esc: esc
  };
})();
