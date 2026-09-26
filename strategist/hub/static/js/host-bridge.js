// 宿主端橋：載入擴充 iframe、核對訊息來源、把 window.xhub 呼叫轉給 Hub API。
//
// Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — src/composables/useExtensionFrame.ts：
//   只信任後端回傳的入口 origin；每則訊息同時核對 e.source 與 e.origin；theme.get 由宿主直接回包；
//   events.emit / callExtension 先經後端權限檢查再路由；看門狗判白屏；開發目錄熱重載。
// Randy 修改：純 ES module（無 Vue）、iframe sandbox 不給 popups/forms/modals/top-navigation、
//   跨擴充呼叫由宿主配發獨立編號並只接受目標 iframe 本人的回覆（x-hub ADR 0008 修訂版做法）。
import { bridgeCall, get } from './api.js';
import { collectTheme, onThemeChange, resolvedMode } from './theme.js';
import { confirmDialog, h } from './ui.js';

const WATCHDOG_MS = 8000;
const DEV_POLL_MS = 1500;
const frames = new Set();
const pendingCalls = new Map();
let hostSeq = 0;

export function mountFrame(container, { extId, surface, variant, onOpen, dev }) {
  const overlay = h('div', { class: 'frame-overlay' }, '載入中…');
  const iframe = h('iframe', { sandbox: 'allow-scripts allow-same-origin', referrerpolicy: 'no-referrer', title: extId });
  container.append(iframe, overlay);
  const rec = { extId, surface, variant, iframe, overlay, origin: '', alive: false, onOpen, destroyed: false };
  frames.add(rec);
  load(rec);
  if (dev) startDevPoll(rec);
  return {
    destroy() {
      rec.destroyed = true;
      clearTimeout(rec.watchdog);
      clearInterval(rec.devTimer);
      frames.delete(rec);
      iframe.src = 'about:blank';
      iframe.remove();
      overlay.remove();
    },
    reload: () => load(rec),
  };
}

async function load(rec) {
  rec.alive = false;
  rec.origin = '';
  showOverlay(rec, '載入中…', false);
  try {
    const query = new URLSearchParams({ surface: rec.surface });
    if (rec.variant) query.set('variant', rec.variant);
    const info = await get(`/api/extensions/${encodeURIComponent(rec.extId)}/frame?${query}`);
    if (rec.destroyed) return;
    rec.origin = info.origin; // 只信任後端給的來源，不隨 iframe 自行導航改變
    const url = new URL(info.url);
    url.searchParams.set('xhub-theme', resolvedMode()); // 首幀就對齊 color-scheme，避免暗色下畫出不透明白底
    rec.iframe.src = url.toString();
    clearTimeout(rec.watchdog);
    rec.watchdog = setTimeout(() => {
      if (!rec.alive && !rec.destroyed) showOverlay(rec, '擴充載入失敗（頁面空白）：請到擴充中心看錯誤訊息', true);
    }, WATCHDOG_MS);
  } catch (err) {
    showOverlay(rec, `無法載入擴充：${err.message}`, true);
  }
}

function showOverlay(rec, text, isError) {
  rec.overlay.textContent = text;
  rec.overlay.className = `frame-overlay${isError ? ' error' : ''}`;
  rec.overlay.hidden = false;
}

function post(rec, msg) {
  if (!rec.origin || !rec.iframe.contentWindow) return;
  rec.iframe.contentWindow.postMessage({ __xhub: true, ...msg }, rec.origin);
}

function findRecord(source) {
  for (const rec of frames) if (rec.iframe.contentWindow === source) return rec;
  return null;
}

window.addEventListener('message', (e) => {
  const rec = findRecord(e.source);
  if (!rec || !rec.origin || e.origin !== rec.origin) return; // 來源視窗 + origin 雙重核對
  const m = e.data;
  if (!m || m.__xhub !== true) return;
  if (!rec.alive) {
    rec.alive = true;
    clearTimeout(rec.watchdog);
    rec.overlay.hidden = true;
    if (rec.variant) post(rec, { type: 'variant', variant: rec.variant });
  }
  handle(rec, m).catch(() => {});
});

async function handle(rec, m) {
  if (m.type === 'call') return handleCall(rec, m);
  if (m.type === 'open') return handleOpen(rec, m);
  if (m.type === 'open-external') return handleOpenExternal(rec, String(m.url || ''));
  if (m.type === 'xhub-emit') return handleEmit(rec, m);
  if (m.type === 'xhub-call') return handleCrossCall(rec, m);
  if (m.type === 'xhub-call-result') return handleCrossResult(rec, m);
  return undefined;
}

async function handleCall(rec, m) {
  if (typeof m.id !== 'number') return;
  const reply = (payload) => post(rec, { type: 'result', id: m.id, ...payload });
  if (m.namespace === 'theme' && m.method === 'get') {
    reply({ ok: true, data: collectTheme() });
    return;
  }
  try {
    const res = await bridgeCall(rec.extId, String(m.namespace || ''), String(m.method || ''), m.args || {});
    reply(res.ok ? { ok: true, data: res.data } : { ok: false, error: res.error });
  } catch (err) {
    reply({ ok: false, error: { code: err.code || 'HOST_ERROR', message: err.message } });
  }
}

function handleOpen(rec, m) {
  const now = Date.now();
  if (now - (rec.lastOpen || 0) < 2000) return; // 防止擴充不斷搶主畫面（安全審查 L2）
  rec.lastOpen = now;
  if (rec.onOpen) rec.onOpen(rec.extId, String(m.surface || 'view'));
}

async function handleOpenExternal(rec, url) {
  if (!/^https?:\/\//i.test(url)) return;
  // 免權限能力：由宿主顯示完整網址讓使用者確認，避免擴充把資料藏在網址裡外送（安全審查 L1）
  if (!(await confirmDialog(`擴充 ${rec.extId} 想用瀏覽器開啟：\n\n${url}`, { okText: '開啟' }))) return;
  await bridgeCall(rec.extId, 'runtime', 'openExternal', { url });
}

async function handleEmit(rec, m) {
  const event = String(m.event || '');
  const res = await bridgeCall(rec.extId, 'events', 'emit', { event, payload: m.payload });
  if (!res.ok) return; // 未宣告 / 未授權 events：靜默丟棄（同 x-hub）
  for (const other of frames) {
    if (other !== rec && other.alive) post(other, { type: 'event', event, payload: m.payload, from: rec.extId });
  }
}

async function handleCrossCall(rec, m) {
  const callerId = m.id;
  const fail = (message) => post(rec, { type: 'xhub-call-result', id: callerId, ok: false, error: { message } });
  const targetId = String(m.targetId || '');
  const method = String(m.method || '');
  const res = await bridgeCall(rec.extId, 'runtime', 'callExtension', { targetId, method });
  if (!res.ok) return fail(`無權呼叫 ${targetId}.${method}`);
  const target = [...frames].find((f) => f.extId === targetId && f.alive);
  if (!target) return fail(`${targetId} 目前沒有開啟（先開啟它的任一形態）`);
  const hostId = ++hostSeq;
  pendingCalls.set(hostId, { caller: rec, callerId, target });
  post(target, { type: 'xhub-call-req', id: hostId, method, payload: m.payload, from: rec.extId });
  setTimeout(() => { if (pendingCalls.delete(hostId)) fail('呼叫逾時'); }, 30000);
  return undefined;
}

function handleCrossResult(rec, m) {
  const pending = pendingCalls.get(m.id);
  if (!pending || pending.target !== rec) return; // 只接受目標 iframe 本人的回覆，防偽造
  pendingCalls.delete(m.id);
  post(pending.caller, { type: 'xhub-call-result', id: pending.callerId, ok: m.ok === true, data: m.data, error: m.error });
}

onThemeChange((theme) => {
  for (const rec of frames) if (rec.alive) post(rec, { type: 'theme', theme });
});

function startDevPoll(rec) {
  let stamp = null;
  rec.devTimer = setInterval(async () => {
    if (document.visibilityState !== 'visible' || rec.destroyed) return;
    try {
      const res = await get(`/api/extensions/${encodeURIComponent(rec.extId)}/stamp`);
      if (stamp === null) stamp = res.stamp;
      else if (res.stamp && res.stamp !== stamp) {
        stamp = res.stamp;
        load(rec);
      }
    } catch (e) { /* 輪詢失敗不影響使用 */ }
  }, DEV_POLL_MS);
}

export function reloadFramesOf(extId) {
  for (const rec of frames) if (rec.extId === extId) load(rec);
}
