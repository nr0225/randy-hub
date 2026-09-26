/*
 * Randy Hub 擴充端橋（注入每個擴充入口 HTML，掛上 window.xhub / window.randyHub）。
 *
 * Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — src-tauri/src/extension.rs XHUB_BRIDGE_SCRIPT。
 * Randy 修改：
 *   - 只與宿主 origin 通訊：送出用 targetOrigin=HUB_ORIGIN（x-hub 用 '*'），收訊同時核對 source 與 origin
 *   - data.* 改為通用代理（Randy Hub 無 x-hub 的筆記/待辦模型，呼叫會以 CAPABILITY_UNAVAILABLE 拒絕）
 *   - 新增 strategist.*（軍師中控）與 notify.show（macOS 通知）
 *   - 載入時就把 ?xhub-variant= 寫到 <html data-xhub-variant>（x-hub pitfall 6c 由宿主代勞）
 */
(function () {
  'use strict';
  var HUB_ORIGIN = '__RANDY_HUB_ORIGIN__';
  var pending = {};
  var seq = 0;
  var listeners = {};
  var exposed = {};

  function send(msg) {
    msg.__xhub = true;
    window.parent.postMessage(msg, HUB_ORIGIN);
  }

  function call(ns, method, args) {
    return new Promise(function (resolve, reject) {
      var id = ++seq;
      pending[id] = { resolve: resolve, reject: reject };
      send({ type: 'call', id: id, namespace: ns, method: method, args: args || {} });
    });
  }

  function emitLocal(event, payload, meta) {
    var arr = listeners[event];
    if (!arr) return;
    for (var i = 0; i < arr.length; i++) {
      // 第二個參數帶來源（由宿主蓋章，擴充無法偽造）：{ from: '<擴充 id>' }；宿主事件為 { from: 'host' }
      try { arr[i](payload, meta || { from: 'host' }); } catch (e) { /* 單一監聽器失敗不影響其他 */ }
    }
  }

  function setVariant(v) {
    var r = document.documentElement;
    if (!r) return;
    r.setAttribute('data-xhub-variant', v || '');
    r.style.setProperty('--xhub-variant', v || '');
  }

  function setMode(mode) {
    var root = document.documentElement;
    if (!root) return;
    var dark = mode === 'dark';
    root.setAttribute('data-xhub-theme', dark ? 'dark' : 'light');
    // color-scheme 必須與宿主一致，否則瀏覽器會替 iframe 畫不透明底（暗色宿主裡出現白底白字）
    root.style.colorScheme = dark ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    var root = document.documentElement;
    if (!root || !theme) return;
    setMode(theme.mode);
    if (theme.preset) root.setAttribute('data-xhub-preset', theme.preset);
    var t = theme.tokens || {};
    var map = {
      '--xhub-accent': t.accent, '--xhub-brand': t.brand, '--xhub-brand-soft': t.brandSoft,
      '--xhub-bg-page': t.bgPage, '--xhub-page-bg': t.pageBg, '--xhub-bg-card': t.bgCard,
      '--xhub-surface': t.surface, '--xhub-text-1': t.text1, '--xhub-text-2': t.text2,
      '--xhub-text-3': t.text3, '--xhub-border': t.border, '--xhub-red': t.red, '--xhub-green': t.green,
      '--xhub-yellow': t.yellow, '--xhub-blue': t.blue, '--xhub-orange': t.orange, '--xhub-radius-lg': t.radiusLg
    };
    for (var k in map) { if (map[k] != null && map[k] !== '') root.style.setProperty(k, map[k]); }
    var wp = theme.wallpaper || {};
    if (wp.on) root.setAttribute('data-xhub-wallpaper', '1'); else root.removeAttribute('data-xhub-wallpaper');
    if (wp.clear) root.setAttribute('data-xhub-wallpaper-clear', '1'); else root.removeAttribute('data-xhub-wallpaper-clear');
    if (wp.immersive) root.setAttribute('data-xhub-immersive', '1'); else root.removeAttribute('data-xhub-immersive');
  }

  function settle(m) {
    var p = pending[m.id];
    if (!p) return;
    delete pending[m.id];
    if (m.ok) { p.resolve(m.data); return; }
    var err = new Error((m.error && m.error.message) || 'xhub error');
    err.code = m.error && m.error.code;
    p.reject(err);
  }

  function answerExposed(m) {
    var h = exposed[m.method];
    var reply = function (ok, data, error) {
      send({ type: 'xhub-call-result', id: m.id, ok: ok, data: data, error: error });
    };
    if (!h) { reply(false, null, { message: 'method not exposed: ' + m.method }); return; }
    try {
      Promise.resolve(h(m.payload)).then(function (d) { reply(true, d); })
        .catch(function (e) { reply(false, null, { message: String((e && e.message) || e) }); });
    } catch (e) { reply(false, null, { message: String((e && e.message) || e) }); }
  }

  window.addEventListener('message', function (e) {
    if (e.source !== window.parent || e.origin !== HUB_ORIGIN) return;
    var m = e.data;
    if (!m || m.__xhub !== true) return;
    if (m.type === 'result' || m.type === 'xhub-call-result') settle(m);
    else if (m.type === 'theme') { applyTheme(m.theme); emitLocal('theme-changed', m.theme); }
    else if (m.type === 'variant') { setVariant(m.variant); emitLocal('xhub:variant-changed', m.variant || ''); }
    else if (m.type === 'event') emitLocal(m.event, m.payload, { from: String(m.from || '') });
    else if (m.type === 'xhub-call-req') answerExposed(m);
  });

  function dataProxy(path) {
    return new Proxy(function () {}, {
      get: function (_t, prop) {
        if (typeof prop !== 'string' || prop === 'then') return undefined;
        return dataProxy(path ? path + '.' + prop : prop);
      },
      apply: function (_t, _this, args) {
        var a = args[0];
        return call('data', path, a && typeof a === 'object' ? a : { id: a });
      }
    });
  }

  var api = {
    call: function (ns, method, args) { return call(ns, method, args || {}); },
    runtime: {
      info: function () { return call('runtime', 'info', {}); },
      open: function (surface) { send({ type: 'open', surface: surface || 'view' }); return Promise.resolve(); },
      callExtension: function (targetId, method, payload) {
        return new Promise(function (resolve, reject) {
          var id = ++seq;
          pending[id] = { resolve: resolve, reject: reject };
          send({ type: 'xhub-call', id: id, targetId: targetId, method: method, payload: payload });
        });
      }
    },
    storage: {
      get: function (k) { return call('storage', 'get', { key: k }); },
      set: function (k, v) { return call('storage', 'set', { key: k, value: v }); },
      remove: function (k) { return call('storage', 'remove', { key: k }); },
      clear: function () { return call('storage', 'clear', {}); }
    },
    sharedStorage: {
      get: function (k) { return call('sharedStorage', 'get', { key: k }); },
      set: function (k, v) { return call('sharedStorage', 'set', { key: k, value: v }); },
      remove: function (k) { return call('sharedStorage', 'remove', { key: k }); }
    },
    config: {
      get: function (k) { return call('config', 'get', { key: k }); },
      set: function (k, v) { return call('config', 'set', { key: k, value: v }); },
      remove: function (k) { return call('config', 'remove', { key: k }); },
      all: function () { return call('config', 'all', {}); }
    },
    data: dataProxy(''),
    fs: {
      saveText: function (a, b) { return call('fs', 'saveText', typeof a === 'object' ? a : { name: a, content: b }); },
      saveFile: function (a, b) { return call('fs', 'saveFile', typeof a === 'object' ? a : { name: a, base64: b }); }
    },
    service: {
      request: function (path, init) {
        init = init || {};
        return call('service', 'request', { path: path, method: init.method, headers: init.headers, body: init.body })
          .then(function (res) {
            return {
              status: res.status, headers: res.headers,
              text: function () { return Promise.resolve(res.body); },
              json: function () { return Promise.resolve(JSON.parse(res.body)); }
            };
          });
      }
    },
    theme: { get: function () { return call('theme', 'get', {}); } },
    strategist: {
      status: function () { return call('strategist', 'status', {}); },
      providers: function () { return call('strategist', 'providers', {}); },
      command: function (text, opts) {
        opts = opts || {};
        return call('strategist', 'command', { command: text, target: opts.target || '', context: opts.context || '' });
      }
    },
    notify: { show: function (opts) { return call('notify', 'show', opts || {}); } },
    openExternal: function (url) { send({ type: 'open-external', url: String(url || '') }); return Promise.resolve(); },
    events: {
      on: function (event, handler) {
        (listeners[event] = listeners[event] || []).push(handler);
        return function () { api.events.off(event, handler); };
      },
      off: function (event, handler) {
        var arr = listeners[event];
        if (!arr) return;
        var i = arr.indexOf(handler);
        if (i >= 0) arr.splice(i, 1);
      },
      emit: function (event, payload) { send({ type: 'xhub-emit', event: event, payload: payload }); return Promise.resolve(); }
    },
    expose: function (method, handler) { exposed[method] = handler; }
  };

  window.xhub = api;
  window.randyHub = api;
  try {
    var query = new URLSearchParams(location.search);
    setVariant(query.get('xhub-variant') || '');
    if (query.get('xhub-theme')) setMode(query.get('xhub-theme'));
  } catch (e) { /* ignore */ }
  call('theme', 'get', {}).then(applyTheme).catch(function () {});
})();
