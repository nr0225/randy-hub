// 擴充中心：已載入清單、權限（風險分級）、service 信任、機密、開發目錄掛載。
// 流程 Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — ExtensionCenter.vue / ExtensionSettingsDialog.vue。
// Randy 修改：高危權限預設關閉並明示；信任 service 時顯示 ADR 0007 要求的顯式告知；無市場（本階段不做）。
import { del, post, put } from './api.js';
import { reloadFramesOf } from './host-bridge.js';
import { nav, store } from './store.js';
import { badge, card, clear, confirmDialog, dot, guard, h, setActions, setTitle } from './ui.js';

const RISK_LABEL = { low: '低', medium: '中', high: '高危' };
const SOURCE_LABEL = { dev: '開發直掛', installed: '已安裝', builtin: '內建範例' };
const TRUST_NOTICE = '信任後，這個擴充的後端會以你的 macOS 使用者身分執行：可以讀寫你的檔案、可以連網，' +
  '等同你本人在終端機執行它。信任不等於程式碼已審查；信任綁「版本＋這個資料夾＋目前的程式碼」，任一項改變都要重新信任。';

export async function renderExtensions(view) {
  setTitle('擴充中心');
  const list = h('div', { class: 'ext-list' });
  const errors = h('div', { class: 'stack' });
  setActions(h('button', { class: 'btn', onclick: () => refresh(true) }, '重新掃描'));
  view.append(mountForm(refresh), errors, list);

  async function refresh(rescan = false) {
    if (rescan) await guard(post('/api/extensions/refresh'), '已重新掃描');
    await nav.refreshExtensions();
    clear(errors).append(...store.extensionErrors.map((e) => h('div', { class: 'errbox' },
      h('strong', {}, `載入失敗：${e.path}`), h('ul', {}, e.errors.map((msg) => h('li', {}, msg))))));
    clear(list).append(...store.extensions.map((ext) => extensionCard(ext, refresh)));
    if (!store.extensions.length) list.append(h('p', { class: 'muted' }, '還沒有擴充。可以掛載一個開發目錄。'));
  }
  await refresh(false);
}

function mountForm(refresh) {
  const input = h('input', { type: 'text', placeholder: '/絕對路徑/到/擴充原始碼資料夾（需含 manifest.json）', style: { flex: '1' } });
  const add = async () => {
    await guard(post('/api/extensions/mounts', { path: input.value.trim() }), '已掛載並載入');
    input.value = '';
    await refresh();
  };
  const mounts = h('div', { class: 'chips' }, store.mounts.map((path) => badge(path)));
  return card('我的擴充（開發目錄直掛，改檔約 1.5 秒自動重載）', h('div', { class: 'stack' },
    h('div', { class: 'row' }, input, h('button', { class: 'btn primary', onclick: add }, '掛載')), mounts), { style: { marginBottom: '14px' } });
}

function header(ext) {
  const icon = h('div', { class: 'ext-icon' }, ext.hasIcon ? h('img', { src: `/ext-icon/${encodeURIComponent(ext.id)}`, alt: '' }) : '◆');
  return h('div', { class: 'row' }, icon, h('div', {},
    h('div', {}, h('strong', {}, ext.name), ' ', h('span', { class: 'muted small' }, `v${ext.version}`)),
    h('div', { class: 'mono muted' }, ext.id)), h('span', { class: 'spacer' }),
  badge(ext.runtime), badge(SOURCE_LABEL[ext.source] || ext.source, ext.source === 'installed' ? '' : 'accent'));
}

function extensionCard(ext, refresh) {
  const body = h('div', { class: 'stack' }, header(ext),
    ext.description ? h('p', { class: 'muted' }, ext.description) : null,
    notices(ext), openButtons(ext, refresh), permissionTable(ext, refresh),
    ext.runtime === 'service' ? serviceSection(ext, refresh) : null, secretSection(ext, refresh),
    h('div', { class: 'muted small mono' }, `身分 ${ext.installKey} · origin ${ext.origin} · ${ext.path}`));
  return card(null, body);
}

function notices(ext) {
  const items = [];
  if (ext.disabledReason) items.push(h('div', { class: 'errbox' }, ext.disabledReason));
  if (ext.missingCapabilities.length) items.push(h('div', { class: 'warnbox' }, `缺能力：${ext.missingCapabilities.join(', ')}（部分功能不會運作）`));
  if (ext.missingDependencies.length) items.push(h('div', { class: 'warnbox' }, `缺依賴：${ext.missingDependencies.join(', ')}`));
  for (const w of ext.warnings) items.push(h('div', { class: 'warnbox' }, w));
  return items.length ? h('div', { class: 'stack' }, items) : null;
}

function openButtons(ext, refresh) {
  const open = (surface) => nav.openSurface(ext.id, surface);
  const buttons = ext.surfaces.filter((s) => s !== 'module').map((s) =>
    h('button', { class: 'btn sm', onclick: () => open(s) }, { view: '開啟頁面', window: '開啟視窗', drawer: '開啟抽屜' }[s]));
  if (ext.surfaces.includes('module')) buttons.push(h('span', { class: 'muted small' }, '含儀表板卡片'));
  const pin = h('label', { class: 'check small' }, h('input', { type: 'checkbox', checked: ext.pinned,
    onchange: async (e) => { await guard(put(`/api/extensions/${encodeURIComponent(ext.id)}/pin`, { pinned: e.target.checked })); await refresh(); } }), '釘選到側欄');
  const unmount = ext.source === 'dev' ? h('button', { class: 'btn sm danger', onclick: async () => {
    await guard(del('/api/extensions/mounts', { path: ext.path }), '已移除掛載');
    await refresh();
  } }, '移除掛載') : null;
  return h('div', { class: 'row' }, buttons, h('span', { class: 'spacer' }), ext.surfaces.includes('view') ? pin : null, unmount);
}

function permissionTable(ext, refresh) {
  if (!ext.grants.length) return h('div', { class: 'muted small' }, '沒有宣告任何權限（只能用 storage / config / theme 等無權限能力）');
  const rows = ext.grants.map((g) => h('tr', {},
    h('td', { class: 'mono' }, g.permission), h('td', {}, badge(RISK_LABEL[g.risk] || g.risk, g.risk)),
    h('td', {}, g.label, g.implemented ? '' : h('span', { class: 'muted' }, '（Hub 尚未提供）')),
    h('td', {}, h('input', { type: 'checkbox', checked: g.granted, onchange: async (e) => {
      if (e.target.checked && g.risk === 'high' && !(await confirmDialog(`授予高危權限「${g.permission}」？\n${g.label}`, { okText: '授予', danger: true }))) { e.target.checked = false; return; }
      await guard(put(`/api/extensions/${encodeURIComponent(ext.id)}/grants`, { permission: g.permission, granted: e.target.checked }), '權限已更新');
      reloadFramesOf(ext.id);
      await refresh();
    } }))));
  return h('div', {}, h('h3', {}, '權限（宣告 × 你的授權，兩者都成立才生效；高危預設關閉）'),
    h('table', { class: 'grid' }, h('thead', {}, h('tr', {}, h('th', {}, '權限'), h('th', {}, '風險'), h('th', {}, '用途'), h('th', {}, '授權'))), h('tbody', {}, rows)));
}

function serviceSection(ext, refresh) {
  const svc = ext.service || {};
  const id = encodeURIComponent(ext.id);
  const trust = h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ext.trusted, onchange: async (e) => {
    if (e.target.checked && !(await confirmDialog(`${TRUST_NOTICE}\n\n確定信任 ${ext.name} v${ext.version}？`, { okText: '信任此版本', danger: true }))) { e.target.checked = false; return; }
    await guard(put(`/api/extensions/${id}/trust`, { trusted: e.target.checked }));
    await refresh();
  } }), `信任此版本（v${ext.version}）的後端`);
  const action = svc.running
    ? h('button', { class: 'btn sm', onclick: async () => { await guard(post(`/api/extensions/${id}/service/stop`), '後端已停止'); await refresh(); } }, '停止後端')
    : h('button', { class: 'btn sm primary', disabled: !ext.trusted, onclick: async () => {
      await guard(post(`/api/extensions/${id}/service/start`), '後端已啟動');
      reloadFramesOf(ext.id);
      await refresh();
    } }, '啟動後端');
  const changed = ext.trustState === 'code-changed'
    ? h('div', { class: 'errbox' }, '程式碼在信任之後有變更（版本號沒變）：後端已停止信任，請確認內容後重新勾選。') : null;
  return h('div', { class: 'stack' }, h('h3', {}, `後端（${ext.backend ? ext.backend.engine : '?'}，監聽 ${ext.backend ? ext.backend.host : '?'}）`),
    h('div', { class: 'warnbox' }, TRUST_NOTICE), changed, h('div', { class: 'row' }, trust, h('span', { class: 'spacer' }),
      dot(svc.running ? svc.ready : null), h('span', { class: 'small' }, svc.running ? `執行中 pid ${svc.pid} · port ${svc.port}` : '未執行'), action),
    h('div', { class: 'muted small mono' }, `日誌：${svc.logPath || '—'}`));
}

function secretSection(ext, refresh) {
  const id = encodeURIComponent(ext.id);
  const name = h('input', { type: 'text', placeholder: '名稱，例如 OPENAI_KEY', style: { width: '160px' } });
  const value = h('input', { type: 'password', placeholder: '值（只寫入 Keychain，不會顯示）', style: { flex: '1' } });
  const save = async () => {
    await guard(put(`/api/extensions/${id}/secrets`, { name: name.value.trim(), value: value.value }), '已存入 Keychain');
    value.value = '';
    await refresh();
  };
  const chips = ext.secrets.map((n) => h('span', { class: 'badge' }, `🔑 ${n} `, h('button', { class: 'btn ghost sm', title: '刪除',
    onclick: async () => { await guard(del(`/api/extensions/${id}/secrets`, { name: n })); await refresh(); } }, '✕')));
  return h('details', {}, h('summary', { class: 'small' }, `機密（${ext.secrets.length}）— 只有此擴充自己的後端能用令牌取回`),
    h('div', { class: 'stack', style: { marginTop: '8px' } }, h('div', { class: 'chips' }, chips),
      h('div', { class: 'row' }, name, value, h('button', { class: 'btn sm', onclick: save }, '儲存'))));
}
