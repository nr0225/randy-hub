// Echo 後端 view：前端一律走 xhub.service.request（由 Hub 代轉），不直接打後端埠。
(async function () {
  const $ = (id) => document.getElementById(id);
  const out = (value) => { $('out').textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); };

  async function refreshState() {
    const info = await window.xhub.runtime.info();
    $('state').textContent = info.serviceReady ? '執行中 ✓' : '未執行（預設不啟動）';
  }

  async function call(path) {
    try {
      const res = await window.xhub.service.request(path);
      out(await res.json());
    } catch (err) {
      out(`${err.code || 'ERROR'}：${err.message}`);
    }
    refreshState();
  }

  $('echo').addEventListener('click', () => call(`/api/echo?msg=${encodeURIComponent($('msg').value)}`));
  $('who').addEventListener('click', () => call('/api/whoami'));
  $('secret').addEventListener('click', () => call('/api/secret-check'));
  await refreshState();
})();
