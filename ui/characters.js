/* Rivulet 若 —— 统一角色组件：DeepSeek 鲸鱼娘(大肥鱼)
 * 素材: CC BY-NC-SA 4.0 / 上善无形「溟月」原型 · ZipZipPipe 女仆装二创
 * 详见项目根目录 ASSETS-LICENSE.md
 */
(function () {
  'use strict';
  var ART = 'assets/whale-girl.png';
  var HEAD = ART;

  function html(opts) {
    opts = opts || {};
    var size = opts.size || 'md';
    var state = opts.state || 'idle';
    var tag = opts.tag || '';
    var name = opts.name || '';
    var big = (size === 'lg' || size === 'xl' || size === 'md');
    return '<div class="rvFish" data-state="' + state + '" data-size="' + size + '">' +
      '<div class="rvFishGlow"></div>' +
      '<div class="rvFishBody"><img src="' + (big ? ART : HEAD) + '" alt="DeepSeek 鲸鱼娘 大肥鱼" draggable="false"></div>' +
      '<div class="rvFishBubble"><i></i><i></i><i></i></div>' +
      (tag ? '<div class="rvFishTag">' + tag + '</div>' : '') +
      (name ? '<div class="rvFishName">' + name + '</div>' : '') +
      '</div>';
  }

  function mount(root) {
    root = root || document;
    var list = root.querySelectorAll('.rvFish');
    for (var i = 0; i < list.length; i++) {
      (function (el) {
        if (el.__bound) return;
        el.__bound = 1;
        el.addEventListener('pointerenter', function () { el.classList.add('is-hover'); });
        el.addEventListener('pointerleave', function () { el.classList.remove('is-hover'); });
        el.addEventListener('click', function () {
          el.classList.remove('is-bounce');
          void el.offsetWidth;
          el.classList.add('is-bounce');
          setTimeout(function () { el.classList.remove('is-bounce'); }, 720);
        });
      })(list[i]);
    }
  }

  function setState(el, state) { if (el) el.setAttribute('data-state', state || 'idle'); }
  function inject(sel, opts) {
    var el = document.querySelector(sel);
    if (!el) return null;
    el.innerHTML = html(opts);
    mount(el);
    return el.querySelector('.rvFish');
  }
  window.RivuletFish = { html: html, mount: mount, setState: setState, inject: inject, src: ART };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { mount(); });
  else mount();
})();
