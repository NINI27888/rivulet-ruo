/* Rivulet 若 —— 自动把「一张大卡里塞多个 h2 区段」拆成多个小卡片
 * 只做 DOM 重排(移动节点, 事件监听保留), 不改任何业务逻辑。
 */
(function () {
  'use strict';

  var HOSTS = '.svcSide, .svcMain, .glass.card, .wsLeft .wsColBody, .wsRight .wsColBody, .boardMain';
  var seen = new WeakSet();

  function splitOnce(el) {
    if (!el || seen.has(el)) return;
    var kids = Array.prototype.slice.call(el.children);
    var groups = [], cur = null;
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i];
      if (k.tagName === 'H2' || (k.classList && k.classList.contains('wsColHead'))) {
        cur = { h: k, items: [] };
        groups.push(cur);
      } else if (cur) {
        cur.items.push(k);
      } else {
        cur = { h: null, items: [k] };
        groups.push(cur);
      }
    }
    var withHead = groups.filter(function (g) { return g.h; }).length;
    if (withHead < 2) { seen.add(el); return; }   // 只有一个区段就不拆
    var frag = document.createDocumentFragment();
    groups.forEach(function (g) {
      var d = document.createElement('div');
      d.className = 'miniCard';
      if (g.h) d.appendChild(g.h);
      g.items.forEach(function (it) { d.appendChild(it); });
      frag.appendChild(d);
    });
    el.innerHTML = '';
    el.appendChild(frag);
    el.classList.add('splitHost');
    seen.add(el);
  }

  function scan(root) {
    var list = (root || document).querySelectorAll(HOSTS);
    for (var i = 0; i < list.length; i++) splitOnce(list[i]);
  }

  function boot() {
    scan(document);
    // 页面切换/数据刷新后重新扫(节点被重建时 WeakSet 不会命中新节点)
    var mo = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var t = muts[i].target;
        if (!t || !t.closest) continue;
        var host = t.closest(HOSTS);
        if (host && !host.classList.contains('splitHost')) splitOnce(host);
      }
    });
    mo.observe(document.body, { childList: true, subtree: true });
    document.addEventListener('click', function () { setTimeout(function () { scan(document); }, 60); }, true);
  }

  window.RivuletCards = { scan: scan };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
