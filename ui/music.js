/* 背景音乐播放器 —— 像素风 / 平静 / 治愈
 * 3 首内置循环曲目(ui/audio/*.wav, 纯合成无版权), 可关、可切、可调音量。
 * 选择存 localStorage, 下次启动沿用。
 */
(function () {
  'use strict';

  var TRACKS = [
    { id: 'night-greenhouse', name: '夜色温室', desc: '温和的三角波 · 像夜里温室的水汽', file: 'audio/night-greenhouse.wav' },
    { id: 'rain-path', name: '雨后小径', desc: '轻轻的方波琶音 · 走在湿漉漉的小路上', file: 'audio/rain-path.wav' },
    { id: 'stardust-lullaby', name: '星屑摇篮', desc: '铃音 + 长延迟 · 适合发呆和睡前', file: 'audio/stardust-lullaby.wav' }
  ];
  var KEY_TRACK = 'ruo.bgm.track';
  var KEY_ON = 'ruo.bgm.on';
  var KEY_VOL = 'ruo.bgm.vol';

  var audio = null;
  var idx = 0;
  var playing = false;
  var vol = 0.45;
  var ui = {};

  function ls(k, d) {
    try { var v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; }
  }
  function lsSet(k, v) {
    try { localStorage.setItem(k, v); } catch (e) {}
  }

  function ensureAudio() {
    if (audio) return audio;
    audio = new Audio();
    audio.loop = true;
    audio.preload = 'auto';
    audio.volume = vol;
    audio.addEventListener('ended', function () { if (playing) audio.play().catch(function () {}); });
    audio.addEventListener('error', function () {
      if (ui.state) ui.state.textContent = '这首放不出来';
    });
    return audio;
  }

  function load(i, autoplay) {
    idx = (i + TRACKS.length) % TRACKS.length;
    var t = TRACKS[idx];
    var a = ensureAudio();
    a.src = t.file;
    a.volume = vol;
    lsSet(KEY_TRACK, t.id);
    render();
    if (autoplay) play();
  }

  function play() {
    var a = ensureAudio();
    playing = true;
    lsSet(KEY_ON, '1');
    var pr = a.play();
    if (pr && pr.catch) {
      pr.catch(function () {
        playing = false;
        lsSet(KEY_ON, '0');
        render();
        if (ui.state) ui.state.textContent = '点一下播放';
      });
    }
    render();
  }

  function pause() {
    playing = false;
    if (audio) audio.pause();
    lsSet(KEY_ON, '0');
    render();
  }

  function toggle() { if (playing) pause(); else play(); }

  function next(dir) { load(idx + (dir || 1), playing); }
  function setVol(v) {
    vol = Math.max(0, Math.min(1, v));
    if (audio) audio.volume = vol;
    lsSet(KEY_VOL, String(vol));
    render();
  }

  function render() {
    var t = TRACKS[idx];
    if (ui.name) ui.name.textContent = t.name;
    if (ui.desc) ui.desc.textContent = t.desc;
    if (ui.btn) {
      // 图标交给 CSS(.bgmDock.playing 切换 play/pause SVG),不要用 textContent 覆盖
      ui.btn.title = playing ? '暂停' : '播放';
      ui.btn.classList.toggle('on', playing);
      var dock = document.getElementById('bgmCard');
      if (dock) dock.classList.toggle('playing', playing);
    }
    if (ui.vol) ui.vol.value = String(Math.round(vol * 100));
    if (ui.state) ui.state.textContent = playing ? '正在播放 · 循环' : '已关闭';
    if (ui.dots) {
      var i;
      for (i = 0; i < ui.dots.length; i++) ui.dots[i].classList.toggle('on', i === idx);
    }
  }

  function mount() {
    ui.name = document.getElementById('bgmName');
    ui.desc = document.getElementById('bgmDesc');
    ui.btn = document.getElementById('bgmPlay');
    ui.vol = document.getElementById('bgmVol');
    ui.state = document.getElementById('bgmState');
    ui.dots = document.querySelectorAll('#bgmDots i');
    if (!ui.btn) return;

    ui.btn.addEventListener('click', toggle);
    var prev = document.getElementById('bgmPrev'), nx = document.getElementById('bgmNext');
    if (prev) prev.addEventListener('click', function () { next(-1); });
    if (nx) nx.addEventListener('click', function () { next(1); });
    if (ui.vol) ui.vol.addEventListener('input', function () { setVol(Number(this.value) / 100); });

    var dots = document.querySelectorAll('#bgmDots i');
    for (var i = 0; i < dots.length; i++) {
      (function (k) {
        dots[i].addEventListener('click', function () { load(k, true); });
      })(i);
    }

    var savedVol = Number(ls(KEY_VOL, '0.45'));
    vol = isFinite(savedVol) ? Math.max(0, Math.min(1, savedVol)) : 0.45;
    var savedId = ls(KEY_TRACK, 'stardust-lullaby');
    idx = 0;
    for (var ti = 0; ti < TRACKS.length; ti++) { if (TRACKS[ti].id === savedId) { idx = ti; break; } }
    ensureAudio();
    audio.src = TRACKS[idx].file;
    audio.volume = vol;

    // 默认不自动放(浏览器/WebView 限制), 用户点一次就记住
    playing = false;
    render();

    var wantOn = ls(KEY_ON, '1') === '1';
    if (wantOn) {
      // 首次用户交互后再尝试恢复, 避免被拦
      var resume = function () {
        document.removeEventListener('pointerdown', resume, true);
        if (!playing) play();
      };
      document.addEventListener('pointerdown', resume, true);
      if (ui.state) ui.state.textContent = '点一下继续播放';
    }
  }

  window.RuoMusic = {
    mount: mount,
    play: play,
    pause: pause,
    toggle: toggle,
    next: next,
    tracks: TRACKS
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
