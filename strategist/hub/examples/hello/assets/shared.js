// Randy 測試擴充：module 與 view 共用的小工具。
window.RandyHello = {
  async gatewayDot(dotEl, textEl) {
    try {
      const status = await window.xhub.strategist.status();
      const gw = status.gateway || {};
      const route = (status.route && status.route.route) || {};
      dotEl.className = `dot ${gw.ok ? 'ok' : 'bad'}`;
      textEl.textContent = gw.ok
        ? `軍師閘道正常 · ${route.STRATEGIST_PROVIDER || '預設'} / ${route.STRATEGIST_MODEL || '預設模型'}`
        : `軍師閘道未回應（${gw.url || '—'}）`;
    } catch (err) {
      dotEl.className = 'dot bad';
      textEl.textContent = `讀不到軍師狀態：${err.code || ''} ${err.message}`;
    }
  },
};
