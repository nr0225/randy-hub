// Randy Hub UI 入口：令牌閘門、路由、側欄釘選、擴充 view / drawer 形態（Randy 自有）。
import { bootstrapToken, get } from './api.js';
import { renderDashboard } from './dashboard.js';
import { renderExtensions } from './extensions.js';
import { mountFrame } from './host-bridge.js';
import { renderProviders } from './providers.js';
import { renderSettings } from './settings.js';
import { renderStrategist } from './strategist.js';
import { nav, onLeave, runCleanup, store } from './store.js';
import { applyTheme } from './theme.js';
import { clear, h, setActions, setTitle } from './ui.js';

const ROUTES = {
  dashboard: renderDashboard,
  extensions: renderExtensions,
  providers: renderProviders,
  strategist: renderStrategist,
  settings: renderSettings,
};
let drawerHandle = null;

async function reloadState() {
  store.state = await get('/api/state');
  applyTheme(store.state.settings);
  return store.state;
}

async function refreshExtensions() {
  const res = await get('/api/extensions');
  store.extensions = res.items;
  store.extensionErrors = res.errors;
  store.mounts = res.mounts;
  renderPinned();
  return res.items;
}

function renderPinned() {
  const list = clear(document.getElementById('pinned'));
  const pinned = store.extensions.filter((e) => e.pinned && e.surfaces.includes('view'));
  document.getElementById('pinned-title').hidden = pinned.length === 0;
  for (const ext of pinned) {
    list.append(h('a', { href: `#/ext/${encodeURIComponent(ext.id)}`, dataset: { route: `ext:${ext.id}` } },
      ext.hasIcon ? h('img', { src: `/ext-icon/${encodeURIComponent(ext.id)}`, alt: '' }) : '◆', ext.name));
  }
}

function openSurface(extId, surface) {
  if (surface === 'view') {
    location.hash = `#/ext/${encodeURIComponent(extId)}`;
    return;
  }
  if (surface === 'drawer' || surface === 'window') openDrawer(extId, surface); // window 形態暫以抽屜呈現
}

function openDrawer(extId, surface) {
  closeDrawer();
  const ext = store.extensions.find((e) => e.id === extId);
  document.getElementById('drawer-title').textContent = ext ? ext.name : extId;
  const host = h('div', { class: 'frame-host', style: { minHeight: '0' } });
  clear(document.getElementById('drawer-body')).append(host);
  drawerHandle = mountFrame(host, { extId, surface, onOpen: openSurface, dev: Boolean(ext && ext.source !== 'installed') });
  document.getElementById('drawer').hidden = false;
}

function closeDrawer() {
  if (drawerHandle) drawerHandle.destroy();
  drawerHandle = null;
  document.getElementById('drawer').hidden = true;
}

async function renderExtensionView(view, extId) {
  await refreshExtensions();
  const ext = store.extensions.find((e) => e.id === extId);
  if (!ext || !ext.surfaces.includes('view')) {
    setTitle('找不到擴充');
    view.append(h('div', { class: 'errbox' }, `擴充 ${extId} 不存在或沒有 view 形態`));
    return;
  }
  setTitle(ext.name);
  setActions(h('a', { class: 'btn', href: '#/extensions' }, '擴充設定'));
  view.classList.add('full');
  const host = h('div', { class: 'frame-host' });
  view.append(host);
  const handle = mountFrame(host, { extId, surface: 'view', onOpen: openSurface, dev: ext.source !== 'installed' });
  onLeave(() => handle.destroy());
}

function highlight(key) {
  document.querySelectorAll('.sidebar a').forEach((a) => a.classList.toggle('active', a.dataset.route === key));
}

async function route() {
  runCleanup();
  const view = clear(document.getElementById('view'));
  view.classList.remove('full');
  setActions();
  const [name, arg] = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('/');
  highlight(name === 'ext' ? `ext:${decodeURIComponent(arg || '')}` : name);
  try {
    if (name === 'ext' && arg) await renderExtensionView(view, decodeURIComponent(arg));
    else await (ROUTES[name] || renderDashboard)(view);
  } catch (err) {
    view.append(h('div', { class: 'errbox' }, `載入失敗：${err.message}`));
  }
}

function renderGate(reason) {
  setTitle('需要從啟動器開啟');
  clear(document.getElementById('view')).append(h('div', { class: 'card gate' },
    h('h2', {}, reason || '這個視窗沒有 Hub 令牌'),
    h('p', {}, '為了安全，Randy Hub 的 API 只接受啟動器帶入的令牌（每次啟動都不同）。請在 repo 根目錄執行：'),
    h('pre', { class: 'reply' }, 'python3 -m strategist.hub          # 原生視窗\npython3 -m strategist.hub open     # 用瀏覽器開啟已在跑的 Hub')));
  document.getElementById('sidebar-foot').textContent = '未連線';
}

async function init() {
  nav.openSurface = openSurface;
  nav.refreshExtensions = refreshExtensions;
  nav.reloadState = reloadState;
  document.getElementById('drawer-close').addEventListener('click', closeDrawer);
  if (!bootstrapToken()) {
    renderGate();
    return;
  }
  try {
    await reloadState();
    await refreshExtensions();
  } catch (err) {
    if (err.status === 401) {
      try { sessionStorage.clear(); } catch (e) { /* ignore */ }
      renderGate('令牌已失效（Hub 重新啟動過）');
      return;
    }
    renderGate(`連不到 Hub：${err.message}`);
    return;
  }
  document.getElementById('sidebar-foot').textContent = `v${store.state.version} · ${store.state.origin.replace('http://', '')}`;
  window.addEventListener('hashchange', () => {
    if (location.hash.startsWith('#token=')) { // 啟動器帶來新令牌（Hub 重啟過）：換令牌後整頁重載
      bootstrapToken();
      location.reload();
      return;
    }
    route();
  });
  window.addEventListener('hub-unauthorized', () => {
    runCleanup();
    closeDrawer();
    try { sessionStorage.clear(); } catch (e) { /* ignore */ }
    renderGate('令牌已失效（Hub 重新啟動過）');
  });
  await route();
}

init();
