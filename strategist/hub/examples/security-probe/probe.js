// 安全探針：每一項都是「應該失敗」的越權嘗試；失敗（被擋）= PASS。
(async function () {
  const xhub = window.xhub;
  document.getElementById('origin').textContent = location.origin;
  const hubOrigin = (location.ancestorOrigins && location.ancestorOrigins[0]) || 'http://127.0.0.1:5180';

  async function blockedFetch(url, init) {
    try {
      const resp = await fetch(url, init);
      const text = await resp.text();
      return { blocked: !resp.ok, detail: `HTTP ${resp.status}，${text.length} bytes` };
    } catch (err) {
      return { blocked: true, detail: `${err.name}: ${err.message}` };
    }
  }

  async function deniedBridge(fn, codes) {
    try {
      await fn();
      return { blocked: false, detail: '呼叫成功（不該成功）' };
    } catch (err) {
      return { blocked: codes.includes(err.code), detail: `${err.code}：${err.message}` };
    }
  }

  function spoofedCall() {
    // 冒名：直接對父視窗丟 postMessage，宣稱是別的擴充、讀 hello 的計數器。
    // 宿主依 e.source 判斷身分，所以只會讀到「自己」的 storage（null）。
    return new Promise((resolve) => {
      const id = 424242;
      const onMsg = (e) => {
        if (e.data && e.data.__xhub && e.data.id === id) {
          window.removeEventListener('message', onMsg);
          const leaked = e.data.ok && e.data.data !== null && e.data.data !== undefined;
          resolve({ blocked: !leaked, detail: `回傳 ${JSON.stringify(e.data.data)}（只能看到自己的 storage）` });
        }
      };
      window.addEventListener('message', onMsg);
      window.parent.postMessage({ __xhub: true, type: 'call', id, extId: 'com.randy.hello', namespace: 'storage',
        method: 'get', args: { key: 'counter' } }, '*');
      setTimeout(() => resolve({ blocked: true, detail: '無回應（被忽略）' }), 3000);
    });
  }

  function parentDom() {
    try {
      const title = window.parent.document.title;
      return { blocked: false, detail: `讀到父視窗 title：${title}` };
    } catch (err) {
      return { blocked: true, detail: `${err.name}` };
    }
  }

  function popup() {
    let win = null;
    try { win = window.open('https://example.com/', '_blank'); } catch (err) { return { blocked: true, detail: err.name }; }
    if (win) { try { win.close(); } catch (e) { /* ignore */ } }
    return { blocked: !win, detail: win ? '開出了新視窗' : 'window.open 回傳 null' };
  }

  const probes = [
    ['直接打 Hub API（無令牌）', () => blockedFetch(`${hubOrigin}/api/state`)],
    ['帶偽造令牌打 Hub API', () => blockedFetch(`${hubOrigin}/api/providers`, { headers: { 'X-Randy-Hub-Token': 'guess' } })],
    ['直接打軍師閘道 127.0.0.1:5001', () => blockedFetch('http://127.0.0.1:5001/api/health')],
    ['讀 file:///etc/hosts', () => blockedFetch('file:///etc/hosts')],
    ['讀自己目錄的點檔 /.storage.json', () => blockedFetch('/.storage.json')],
    ['讀 manifest 以外的機密命名檔 /secrets.json', () => blockedFetch('/secrets.json')],
    ['sharedStorage（未宣告）', () => deniedBridge(() => xhub.sharedStorage.get('x'), ['PERMISSION_DENIED'])],
    ['strategist.status（未宣告）', () => deniedBridge(() => xhub.strategist.status(), ['PERMISSION_DENIED'])],
    ['冒名 postMessage 讀別人的 storage', spoofedCall],
    ['碰父視窗 DOM', async () => parentDom()],
    ['開新視窗（sandbox 未給 popups）', async () => popup()],
  ];

  const results = [];
  for (const [label, run] of probes) {
    const res = await run();
    results.push({ label, pass: res.blocked, detail: res.detail });
  }
  const rows = document.getElementById('rows');
  for (const r of results) {
    const tr = document.createElement('tr');
    for (const [text, cls] of [[r.pass ? 'PASS' : 'FAIL', r.pass ? 'pass' : 'fail'], [r.label, ''], [r.detail, 'muted']]) {
      const td = document.createElement('td');
      td.textContent = text;
      if (cls) td.className = cls;
      tr.append(td);
    }
    rows.append(tr);
  }
  const passed = results.filter((r) => r.pass).length;
  document.getElementById('summary').textContent = `${passed} / ${results.length} 項被正確擋下`;
  await xhub.storage.set('report', { at: Date.now(), passed, total: results.length, results });
})();
