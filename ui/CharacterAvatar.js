/* Rivulet 若 —— 统一角色头像组件
 * 素材: assets/whale-avatar.png (由立绘裁切, 头部完整, 透明底)
 */
(function () {
  'use strict';
  var SRC = 'assets/whale-avatar.png';
  var SIZES = { xs: 28, sm: 36, md: 44, lg: 64, xl: 80 };

  function html(opts) {
    opts = opts || {};
    var size = opts.size || 'md';
    var px = SIZES[size] || SIZES.md;
    var cls = opts.className ? ' ' + opts.className : '';
    return '<span class="rvAva" data-size="' + size + '" style="--ava:' + px + 'px">' +
      '<img src="' + SRC + '" alt="大肥鱼" draggable="false" loading="lazy"' +
      ' onerror="this.parentNode.classList.add(\'rvAvaFail\');this.remove();"></span>' + '</span>'.replace('</span></span>', '</span>');
  }

  function mount(root) {
    root = root || document;
    var list = root.querySelectorAll('.rvAva img');
    for (var i = 0; i < list.length; i++) {
      (function (img) {
        img.addEventListener('error', function () {
          var p = img.parentNode;
          if (p) p.classList.add('rvAvaFail');
          img.style.display = 'none';
        });
      })(list[i]);
    }
  }

  window.RivuletAvatar = { html: html, mount: mount, src: SRC, sizes: SIZES };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { mount(); });
  else mount();
})();
