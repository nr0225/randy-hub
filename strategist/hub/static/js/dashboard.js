// 儀表板（Bento 網格）：內建卡片 + 擴充 module 卡片，版面可編輯並持久化。
// Bento 概念 Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — 工作台自由網格 / useDashboardLayout.ts；
// Randy 修改：簡化成「排序 + 隱藏」編輯，卡片內容換成軍師中控 / 擴充 / 供應商 / 稽核。
import { get, put } from './api.js';
import { mountFrame } from './host-bridge.js';
import { nav, onLeave, store } from './store.js';
import { badge, card, clear, dot, guard, h, kv, setActions, setTitle, timeAgo } from './ui.js';

const BUILTIN = [
  { id: 'clock', title: '時鐘', w: 4, h: 4 },
  { id: 'strategist', title: '軍師中控', w: 8, h: 8 },
  { id: 'extensions', title: '擴充', w: 4, h: 5 },
  { id: 'providers', title: 'AI 供應商', w: 4, h: 5 },
  { id: 'audit', title: '稽核紀錄', w: 4, h: 5 },
];

function moduleCards() {
  return store.extensions.filter((e) => e.surfaces.includes('module') && !e.disabledReason).map((e) => {
    const variant = e.moduleVariants[0] || {};
    return { id: `ext:${e.id}`, title: e.name, ext: e, variant: variant.id || '',
      w: Math.min(12, Math.max(3, variant.idealW || 4)), h: Math.min(12, Math.max(3, (variant.idealH || 3) + 1)) };
  });
}

function mergedLayout(available) {
  const saved = store.state.settings['dashboard.layout'] || [];
  const byId = new Map(available.map((c) => [c.id, c]));
  const seen = new Set();
  const items = [];
  for (const s of saved) {
    if (byId.has(s.id) && !seen.has(s.id)) { items.push({ ...byId.get(s.id), hidden: s.hidden }); seen.add(s.id); }
  }
  for (const c of available) if (!seen.has(c.id)) items.push({ ...c, hidden: false });
  return items;
}

export async function renderDashboard(view) {
  setTitle('儀表板');
  const grid = h('div', { class: 'bento' });
  const tray = h('div', { class: 'hidden-tray' });
  const handles = [];
  const timers = [];
  let editing = false;
  onLeave(() => { handles.splice(0).forEach((x) => x.destroy()); timers.forEach(clearInterval); });
  const editBtn = h('button', { class: 'btn', onclick: () => { editing = !editing; draw(); } }, '編輯版面');
  setActions(editBtn);
  view.append(grid, tray);
  const data = await loadData();

  async function save(items) {
    const layout = items.map((i) => ({ id: i.id, hidden: Boolean(i.hidden) }));
    await guard(put('/api/settings', { 'dashboard.layout': layout }));
    store.state.settings['dashboard.layout'] = layout;
    draw();
  }

  function draw() {
    handles.splice(0).forEach((x) => x.destroy());
    timers.splice(0).forEach(clearInterval);
    clear(grid);
    clear(tray);
    grid.classList.toggle('editing', editing);
    editBtn.textContent = editing ? '完成' : '編輯版面';
    const items = mergedLayout([...BUILTIN, ...moduleCards()]);
    const tools = (index) => layoutTools(items, index, save);
    items.forEach((item, index) => {
      if (!item.hidden) grid.append(renderCard(item, tools(index), data, { handles, timers }));
    });
    const hidden = items.filter((i) => i.hidden);
    if (editing && hidden.length) {
      tray.append(h('h3', {}, '已隱藏的卡片'), h('div', { class: 'chips' }, hidden.map((item) =>
        h('button', { class: 'btn sm', onclick: () => save(items.map((i) => (i.id === item.id ? { ...i, hidden: false } : i))) }, `＋ ${item.title}`))));
    }
  }
  draw();
}

function layoutTools(items, index, save) {
  const move = (delta) => {
    const next = [...items];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    save(next);
  };
  const hide = () => save(items.map((i, k) => (k === index ? { ...i, hidden: true } : i)));
  return h('span', { class: 'layout-tools' },
    h('button', { class: 'btn sm', title: '往前', onclick: () => move(-1) }, '↑'),
    h('button', { class: 'btn sm', title: '往後', onclick: () => move(1) }, '↓'),
    h('button', { class: 'btn sm', title: '隱藏', onclick: hide }, '隱藏'));
}

async function loadData() {
  const [strategist, providers, audit] = await Promise.allSettled([
    get('/api/strategist/status'), get('/api/providers'), get('/api/audit?limit=8')]);
  return {
    strategist: strategist.status === 'fulfilled' ? strategist.value : null,
    providers: providers.status === 'fulfilled' ? providers.value.items : [],
    audit: audit.status === 'fulfilled' ? audit.value.items : [],
  };
}

function renderCard(item, tools, data, bag) {
  const style = { gridColumn: `span ${item.w}`, gridRow: `span ${item.h}` };
  if (item.ext) return moduleCard(item, tools, style, bag);
  const body = {
    clock: () => clockBody(bag),
    strategist: () => strategistBody(data.strategist),
    extensions: () => extensionsBody(),
    providers: () => providersBody(data.providers),
    audit: () => auditBody(data.audit),
  }[item.id]();
  return card(item.title, body, { style, actions: tools });
}

function moduleCard(item, tools, style, bag) {
  const showTitle = item.ext.moduleOptions && item.ext.moduleOptions.defaultHideTitle === false;
  const box = h('div', { class: 'card module-card', style },
    h('div', { class: showTitle ? 'card-title' : 'card-title layout-only' }, item.title, h('span', { class: 'spacer' }), tools));
  const host = h('div', { class: 'frame-host', style: { minHeight: '0' } });
  box.append(host);
  bag.handles.push(mountFrame(host, { extId: item.ext.id, surface: 'module', variant: item.variant,
    onOpen: nav.openSurface, dev: item.ext.source !== 'installed' }));
  return box;
}

function clockBody(bag) {
  const time = h('div', { class: 'clock-time' });
  const date = h('div', { class: 'muted' });
  const tick = () => {
    const now = new Date();
    time.textContent = now.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false });
    date.textContent = now.toLocaleDateString('zh-TW', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' });
  };
  tick();
  bag.timers.push(setInterval(tick, 1000));
  return h('div', {}, time, date);
}

export function strategistBody(s) {
  if (!s) return h('div', { class: 'errbox' }, '讀不到軍師中控狀態');
  const route = (s.route && s.route.route) || {};
  const keys = (s.route && s.route.keysPresent) || {};
  const slots = (s.providers && s.providers.slots) || [];
  const gw = s.gateway || {};
  const ld = s.launchd || {};
  return h('div', { class: 'stack' },
    h('div', { class: 'row' }, dot(gw.ok), h('strong', {}, gw.ok ? '閘道正常' : '閘道未回應'),
      h('span', { class: 'muted small' }, gw.url, gw.latencyMs !== undefined ? ` · ${gw.latencyMs} ms` : '', gw.error ? ` · ${gw.error}` : '')),
    h('div', { class: 'row small' }, dot(ld.loaded ? ld.state === 'running' : null),
      `launchd ${ld.label || ''}：${ld.loaded ? `${ld.state}${ld.pid ? `（pid ${ld.pid}）` : ''}` : '未載入'}`),
    kv([['目前 provider', route.STRATEGIST_PROVIDER || '（預設）'], ['模型', route.STRATEGIST_MODEL || '（預設）'],
      ['設定來源', s.route && s.route.ok ? s.route.source : (s.route && s.route.error) || '—']]),
    h('div', {}, h('h3', {}, 'API key（只顯示有沒有設定）'), h('div', { class: 'chips' },
      Object.keys(keys).length ? Object.entries(keys).map(([k, v]) => badge(`${v ? '✓' : '✗'} ${k}`, v ? 'low' : '')) : h('span', { class: 'muted small' }, '無'))),
    routesTable(s.routes),
    h('div', {}, h('h3', {}, `provider 插槽（${slots.length}）`), h('div', { class: 'chips' },
      slots.map((slot) => h('span', { class: 'badge', title: slot.note || '' }, `${slot.name} ${slot.status || ''}`)))),
    h('div', { class: 'row' }, h('a', { class: 'btn sm', href: '#/strategist' }, '開啟軍師中控頁')));
}

function routesTable(r) {
  // 路由表：route → provider → key slot（只顯示有沒有設定）→ model
  if (!r || !r.ok) return h('div', { class: 'muted small' }, `路由表：${(r && r.error) || '閘道未提供'}`);
  return h('div', {}, h('h3', {}, `路由（${r.routes.length}）`),
    h('table', { class: 'small' }, r.routes.map((row) => h('tr', {},
      h('td', {}, row.route), h('td', {}, row.provider), h('td', {}, row.model || '—'),
      h('td', {}, badge(`${row.keySet ? '✓' : '✗'} ${row.keySlot || '（本機）'}`, row.keySet ? 'low' : ''))))));
}

function extensionsBody() {
  const exts = store.extensions;
  const running = exts.filter((e) => e.service && e.service.running).length;
  return h('div', { class: 'stack' },
    kv([['已載入', String(exts.length)], ['載入失敗', String(store.extensionErrors.length)], ['執行中的後端', String(running)]]),
    h('div', { class: 'chips' }, exts.slice(0, 8).map((e) => badge(e.name, e.source === 'dev' ? 'accent' : ''))),
    h('a', { class: 'btn sm', href: '#/extensions' }, '擴充中心'));
}

function providersBody(items) {
  if (!items.length) return h('div', { class: 'stack' }, h('p', { class: 'muted' }, '還沒有設定 Hub 的 AI 供應商'), h('a', { class: 'btn sm', href: '#/providers' }, '新增供應商'));
  return h('div', { class: 'stack' }, items.map((p) => h('div', { class: 'row' },
    dot(p.lastTest ? p.lastTest.ok : null), h('strong', {}, p.name),
    h('span', { class: 'muted small' }, p.lastTest ? (p.lastTest.ok ? `${p.lastTest.modelCount} 個模型` : p.lastTest.error) : '未測試'))));
}

function auditBody(items) {
  if (!items.length) return h('p', { class: 'muted' }, '尚無紀錄');
  return h('div', { class: 'stack small' }, items.map((a) => h('div', {},
    badge(a.action, a.action === 'permission-denied' ? 'high' : ''), ' ', h('span', { class: 'mono' }, a.actor), ' ',
    h('span', { class: 'muted' }, timeAgo(a.ts)))));
}
