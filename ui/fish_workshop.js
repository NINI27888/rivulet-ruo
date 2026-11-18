/* ============================================================
   鱼的小窝 · 工坊(workshop) / 账本(ledger)
   ------------------------------------------------------------
   本文件只负责「画」和「把点击翻译成动作」:

     window.FishHome.act(body)     执行动作(内部已处理刷新/提示)
     window.FishWorkshop.renderWorkshop(el, snap)   渲染工坊整页
     window.FishWorkshop.renderLedger(el, snap)     渲染账本整页

   刷新快照、切页、显隐容器都由 fish.js 负责,这里不轮询、不自己
   invoke。风格和 fish.js 一致:纯 ES5(var / function),不依赖任
   何外部库,字符串拼接而不用模板字符串。
   ============================================================ */
(function () {
  'use strict';

  /* ---------------- 常量(和引擎约定一致) ---------------- */

  // 采集点与掉落
  var PLACES = [
    { key: 'hill', name: '后山', drops: [
      { name: '木头', pct: 40, color: '#B98D68' },
      { name: '石头', pct: 30, color: '#9AA3A8' },
      { name: '草药', pct: 20, color: '#7FA06A' },
      { name: '铁矿石', pct: 10, color: '#6E6A72' }
    ] },
    { key: 'sea', name: '海边', drops: [
      { name: '贝壳', pct: 34, color: '#EFC9BD' },
      { name: '海带', pct: 34, color: '#4E8C6A' },
      { name: '细沙', pct: 27, color: '#E0D2A6' },
      { name: '珍珠', pct: 5, color: '#DED8E8' }
    ] }
  ];

  // 配方:mats 里的 name 就是 snap.items 的键;id 原样传给 act
  var RECIPES = [
    { id: 'plank', name: '木板', sell: 3, color: '#C08A5E',
      mats: [{ name: '木头', need: 2 }] },
    { id: 'herb_pack', name: '草药包', sell: 6, color: '#7FA06A',
      mats: [{ name: '草药', need: 2 }] },
    { id: 'pearl_necklace', name: '珍珠项链', sell: 30, color: '#D9CFE6',
      mats: [{ name: '珍珠', need: 2 }, { name: '细沙', need: 1 }] }
  ];

  // 物品柜里能卖的东西:显示名/英文 id 都认,统一成 sell_item 的 item
  var SELL = {
    '木板': 'plank', '草药包': 'herb_pack', '珍珠项链': 'pearl_necklace',
    'plank': 'plank', 'herb_pack': 'herb_pack', 'pearl_necklace': 'pearl_necklace'
  };
  var ITEM_CN = { plank: '木板', herb_pack: '草药包', pearl_necklace: '珍珠项链' };
  var ITEM_PRICE = { plank: 3, herb_pack: 6, pearl_necklace: 30 };

  var ITEM_COLOR = {
    '木头': '#B98D68', '石头': '#9AA3A8', '草药': '#7FA06A', '铁矿石': '#6E6A72',
    '贝壳': '#EFC9BD', '海带': '#4E8C6A', '细沙': '#E0D2A6', '珍珠': '#DED8E8',
    '木板': '#C08A5E', '草药包': '#7FA06A', '珍珠项链': '#D9CFE6'
  };

  /* ---------------- 小工具 ---------------- */

  // 统一走 FishHome 的转义;万一它还没挂上,用同款实现兜底
  function esc(s) {
    var H = window.FishHome;
    if (H && typeof H.esc === 'function') return H.esc(s);
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function snapOf(snap) {
    if (snap) return snap;
    var H = window.FishHome;
    if (H && typeof H.snapshot === 'function') return H.snapshot() || {};
    return {};
  }

  function doAct(body) {
    var H = window.FishHome;
    if (H && typeof H.act === 'function') H.act(body);
  }

  function num(v) {
    v = Number(v);
    return isFinite(v) ? v : 0;
  }

  // 0-100 取整,防止引擎给出小数/越界
  function score(v) {
    v = Math.round(num(v));
    if (v < 0) v = 0;
    if (v > 100) v = 100;
    return v;
  }

  function colorOf(name) {
    return ITEM_COLOR[name] || 'rgba(120,140,110,.55)';
  }

  // 10 个圆点:值为 100 时 10 个全亮
  function dots(v) {
    var filled = Math.round(v / 10), i, cls, html = '';
    for (i = 0; i < 10; i++) {
      cls = i < filled ? ('on' + (v <= 30 ? ' low' : '')) : '';
      html += '<b' + (cls ? ' class="' + cls + '"' : '') + '></b>';
    }
    return '<i class="wsDots">' + html + '</i>';
  }

  function cardHead(title, right) {
    return '<div class="wsCardHead"><h2>' + title + '</h2>' + (right || '') + '</div>';
  }

  function chip(text, cls) {
    return '<span class="' + (cls || 'wsChip') + '">' + esc(text) + '</span>';
  }

  // 时间统一显示成「11-09 01:20」:兼容 "11-09 01:20" / "2026-11-09 01:20" / ISO
  function shortAt(v) {
    var at = String(v == null ? '' : v).replace('T', ' ');
    if (/^\d{4}-\d{2}-\d{2}/.test(at)) at = at.slice(5);
    if (at.length > 11) at = at.slice(0, 11);
    return at;
  }

  /* ---------------- 工坊:照顾鲸鱼娘 ---------------- */

  function stat(label, v) {
    var val = score(v);
    return '<div class="wsStat"><span class="wsStatK">' + esc(label) + '</span>' +
      '<b class="wsStatN">' + val + '</b>' + dots(val) + '</div>';
  }

  function careCard(me, her) {
    var where = '';
    if (her && her.text) where = her.text;
    else if (me && me.doing && /[^\x00-\x7f]/.test(me.doing)) where = me.doing;
    else if (me && me.floor != null) where = '在 ' + num(me.floor) + ' 楼';
    return '<div class="glass card">' +
      cardHead('照顾鲸鱼娘', where ? chip(where) : '') +
      '<div class="wsStats">' +
        stat('能量', me.energy) + stat('心情', me.mood) +
        stat('饱腹', me.full) + stat('清洁', me.clean) +
      '</div>' +
      '<div class="wsBtnRow">' +
        '<button type="button" class="wsBtn" data-ws="care" data-job="bathe">叫她去洗个澡</button>' +
        '<button type="button" class="wsBtn" data-ws="care" data-job="eat">让她吃点东西</button>' +
        '<button type="button" class="wsBtn" data-ws="care" data-job="play">陪她玩一会</button>' +
      '</div>' +
      '<p class="wsHint">饿了、脏了心情都会往下掉;先喂饱她,再让她去洗个澡,陪玩能把心情拉回来。</p>' +
      '</div>';
  }

  /* ---------------- 工坊:采集 ---------------- */

  function placeCol(place) {
    var i, d, drops = '';
    for (i = 0; i < place.drops.length; i++) {
      d = place.drops[i];
      drops += '<span class="wsDrop"><i style="background:' + d.color + '"></i>' +
        esc(d.name) + '<em>' + d.pct + '%</em></span>';
    }
    return '<div class="wsPlace">' +
      '<div class="wsPlaceTop"><b>' + esc(place.name) + '</b></div>' +
      '<div class="wsDrops">' + drops + '</div>' +
      '<button type="button" class="wsGo" data-ws="gather" data-place="' + place.key + '">' +
        '让她去 · 能量 -8</button>' +
      '</div>';
  }

  function gatherCard(me) {
    var i, cols = '';
    for (i = 0; i < PLACES.length; i++) cols += placeCol(PLACES[i]);
    var low = (me.energy != null) && score(me.energy) < 8;
    var html = '<div class="glass card">' +
      cardHead('采集', chip('一趟 20 分钟 · 捡 2 样')) +
      '<div class="wsGather">' + cols + '</div>' +
      '<p class="wsHint">每处最多攒 6 份,捡一次少一份,40 分钟长回一份。东西在路上捡,她回来才放进物品柜。' +
        (low ? '<b class="wsWarn">她能量不够了,先让她吃点东西。</b>' : '') + '</p>' +
      '</div>';
    return html;
  }

  /* ---------------- 工坊:配方 ---------------- */

  function recipeRow(r, items) {
    var i, m, own, can = true, mats = '';
    for (i = 0; i < r.mats.length; i++) {
      m = r.mats[i];
      own = num(items[m.name]);
      if (own < m.need) can = false;
      mats += '<span class="wsMat' + (own < m.need ? ' lack' : '') + '">' + esc(m.name) +
        ' <b>' + own + '</b>/' + m.need + '</span>';
    }
    return '<div class="wsRecipe">' +
      '<i class="wsSw" style="background:' + r.color + '"></i>' +
      '<div class="wsRecipeMain">' +
        '<div class="wsRecipeName"><b>' + esc(r.name) + '</b>' +
          '<span>卖 ' + r.sell + ' 金币</span></div>' +
        '<div class="wsMats">' + mats + '</div>' +
      '</div>' +
      '<button type="button" class="wsMake" data-ws="craft" data-recipe="' + r.id + '"' +
        (can ? '' : ' disabled') + '>' + (can ? '制作' : '缺材料') + '</button>' +
      '</div>';
  }

  function recipeCard(items) {
    var i, rows = '';
    for (i = 0; i < RECIPES.length; i++) rows += recipeRow(RECIPES[i], items);
    return '<div class="glass card">' +
      cardHead('配方') +
      '<div class="wsRecipes">' + rows + '</div>' +
      '<p class="wsHint">做出来的东西先放物品柜,可以在右边点「卖 1」换成金币。</p>' +
      '</div>';
  }

  /* ---------------- 工坊:物品柜 ---------------- */

  // 卖价:引擎给的 snap.item_prices 可能按中文名索引,老写法 ITEM_PRICE 按英文 id 索引,两种都认
  function itemPrice(priceMap, label, id) {
    var pr = priceMap ? priceMap[label] : 0;
    if (!pr && id) pr = ITEM_PRICE[id];
    return num(pr);
  }

  function itemsCard(items, snap) {
    var keys = [], value = 0, i, k, id, qty, label, rows;
    var priceMap = (snap && snap.item_prices) || null;
    for (k in items) {
      if (!Object.prototype.hasOwnProperty.call(items, k)) continue;
      if (num(items[k]) > 0) keys.push(k);
    }
    rows = '';
    for (i = 0; i < keys.length; i++) {
      k = keys[i];
      qty = num(items[k]);
      id = SELL[k];
      label = ITEM_CN[k] || k;
      value += itemPrice(priceMap, label, id) * qty;
      rows += '<div class="wsItem">' +
        '<i class="wsSw" style="background:' + colorOf(label) + '"></i>' +
        '<b>' + esc(label) + '</b><span>×' + qty + '</span>' +
        (id ? '<button type="button" class="wsSell" data-ws="sell" data-item="' + id + '">卖 1</button>' : '') +
        '</div>';
    }
    return '<div class="glass card">' +
      cardHead('物品柜', chip('值 ' + value + ' 金币')) +
      (keys.length ? '<div class="wsItems">' + rows + '</div>'
        : '<p class="wsHint wsEmpty">空的。采集、合成得到的东西都放这里。</p>') +
      '</div>';
  }

  /* ---------------- 工坊:动静 ---------------- */

  function eventsCard(events) {
    var list = (events || []).slice(-6).reverse(), i, e, rows = '';
    for (i = 0; i < list.length; i++) {
      e = list[i] || {};
      rows += '<div class="wsLogRow"><span>' + esc(shortAt(e.at)) + '</span><div>' +
        esc(e.text || '') + '</div></div>';
    }
    return '<div class="glass card">' +
      cardHead('工坊的动静') +
      (rows ? '<div class="wsLog">' + rows + '</div>'
        : '<p class="wsHint wsEmpty">还没有动静。</p>') +
      '</div>';
  }

  /* ---------------- 工坊:整页 ---------------- */

  function renderWorkshop(el, snap) {
    if (!el) return;
    var s = snapOf(snap);
    var chars = s.characters || [];
    var me = null, i;
    for (i = 0; i < chars.length; i++) {
      if (chars[i] && chars[i].id === 'jingyu') { me = chars[i]; break; }
    }
    if (!me) me = chars[0] || {};

    el.innerHTML =
      '<div class="wsCol">' +
        careCard(me, s.her || {}) +
        gatherCard(me) +
        recipeCard(s.items || {}) +
      '</div>' +
      '<div class="wsCol">' +
        itemsCard(s.items || {}, s) +
        eventsCard(s.events) +
      '</div>';
    bind(el);
  }

  /* ---------------- 账本 ---------------- */

  // 金额字段:真机老数据的字段叫 amount,契约里叫 delta,两个都认(0 也是有效值)
  function amountOf(x) {
    if (!x) return 0;
    if (x.amount != null) return num(x.amount);
    return num(x.delta);
  }

  // 时间字符串(统一成 11-09 01:20)当排序键;同年内按字符串比就是时间序
  function ledgerKey(x) {
    return shortAt(x && (x.at || x.time));
  }

  // 新的在最上面:按时间倒序,同一时间保持原顺序(稳定)
  function newestFirst(list) {
    var arr = [], out = [], i;
    for (i = 0; i < list.length; i++) arr.push({ i: i, k: ledgerKey(list[i]), x: list[i] });
    arr.sort(function (a, b) {
      if (a.k > b.k) return -1;
      if (a.k < b.k) return 1;
      return a.i - b.i;
    });
    for (i = 0; i < arr.length; i++) out.push(arr[i].x);
    return out;
  }

  function ledgerRow(x) {
    x = x || {};
    var d = amountOf(x);
    var cls = d > 0 ? 'in' : (d < 0 ? 'out' : 'zero');
    // 0 值(老数据)显示灰色的「—」,不抢眼
    var txt = d > 0 ? ('+' + d) : (d < 0 ? String(d) : '—');
    return '<div class="ledgerRow">' +
      '<span class="ledgerAt">' + esc(shortAt(x.at || x.time)) + '</span>' +
      '<span class="ledgerNote">' + esc(x.note || '') + '</span>' +
      '<b class="ledgerDelta ' + cls + '">' + esc(txt) + '</b>' +
      '</div>';
  }

  function renderLedger(el, snap) {
    if (!el) return;
    var s = snapOf(snap);
    var list = s.ledger || [];
    var rows = newestFirst(list);
    var coins = num(s.coins);
    var income = 0, spend = 0, i, d, html = '';
    for (i = 0; i < rows.length; i++) {
      d = amountOf(rows[i]);
      if (d > 0) income += d;
      else if (d < 0) spend += d;
      html += ledgerRow(rows[i]);
    }
    var inTxt = income > 0 ? ('+' + income) : '0';
    var outTxt = spend < 0 ? String(spend) : '0';

    el.innerHTML = '<div class="glass card ledgerCard">' +
      '<div class="wsCardHead">' +
        '<h2>账本</h2>' +
        chip('最近 ' + rows.length + ' 笔') +
        '<span class="ledgerBal">余额 <b>' + coins + '</b> 金币</span>' +
        '<span class="wsChip ledgerSum" style="margin-left:0">收入 <b class="ledgerDelta in" style="font-size:11px">' +
          esc(inTxt) + '</b> · 支出 <b class="ledgerDelta out" style="font-size:11px">' +
          esc(outTxt) + '</b></span>' +
      '</div>' +
      (rows.length ? '<div class="ledgerList">' + html + '</div>'
        : '<p class="wsHint wsEmpty">还没有流水。</p>') +
      '</div>';
  }

  /* ---------------- 事件绑定(每个容器只绑一次) ---------------- */

  function delegate(el, e) {
    var t = e.target, n = 0;
    while (t && t !== el && n < 8) {
      if (t.getAttribute && t.getAttribute('data-ws')) break;
      t = t.parentNode; n++;
    }
    if (!t || t === el || !t.getAttribute) return;
    var kind = t.getAttribute('data-ws');
    if (t.disabled) return;
    if (kind === 'care') {
      doAct({ type: 'care', job: t.getAttribute('data-job') });
    } else if (kind === 'gather') {
      doAct({ type: 'gather', place: t.getAttribute('data-place') });
    } else if (kind === 'craft') {
      doAct({ type: 'craft', recipe: t.getAttribute('data-recipe') });
    } else if (kind === 'sell') {
      doAct({ type: 'sell_item', item: t.getAttribute('data-item'), qty: 1 });
    }
  }

  function bind(el) {
    if (el.__wsBound) return;
    el.__wsBound = true;
    el.addEventListener('click', function (e) { delegate(el, e); });
  }

  /* ---------------- 对外 ---------------- */

  window.FishWorkshop = {
    renderWorkshop: renderWorkshop,
    renderLedger: renderLedger
  };
})();
