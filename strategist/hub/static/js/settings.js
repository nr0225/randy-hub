// 設定：主題、Strategist 連線、備份、健康檢查、稽核、授權聲明（Randy 自有）。
import { get, post, put } from './api.js';
import { applyTheme } from './theme.js';
import { nav, store } from './store.js';
import { badge, card, dot, guard, h, kv, setTitle, timeAgo } from './ui.js';

export async function renderSettings(view) {
  setTitle('設定');
  const s = store.state.settings;
  view.append(h('div', { class: 'bento' },
    card('外觀', themeForm(s), { style: { gridColumn: 'span 6', gridRow: 'span 4' } }),
    card('軍師中控連線', strategistForm(s), { style: { gridColumn: 'span 6', gridRow: 'span 4' } }),
    card('資料與備份', backupBox(), { style: { gridColumn: 'span 6', gridRow: 'span 5' } }),
    card('健康檢查', await doctorBox(), { style: { gridColumn: 'span 6', gridRow: 'span 5' } }),
    card('稽核紀錄（最近 50 筆）', await auditBox(), { style: { gridColumn: 'span 8', gridRow: 'span 8' } }),
    card('關於與授權', aboutBox(), { style: { gridColumn: 'span 4', gridRow: 'span 8' } })));
}

async function saveSettings(updates, message) {
  await guard(put('/api/settings', updates), message);
  const state = await nav.reloadState();
  applyTheme(state.settings);
}

function themeForm(s) {
  const mode = h('select', {}, [['system', '跟隨系統'], ['light', '亮色'], ['dark', '暗色']].map(([v, t]) =>
    h('option', { value: v, selected: (s['theme.mode'] || 'system') === v }, t)));
  const accent = h('input', { type: 'color', value: s['theme.accent'] || '#5b5bf5' });
  mode.addEventListener('change', () => saveSettings({ 'theme.mode': mode.value }, '主題已更新'));
  accent.addEventListener('change', () => saveSettings({ 'theme.accent': accent.value }, '強調色已更新'));
  const examples = h('input', { type: 'checkbox', checked: s['hub.loadExamples'] !== false });
  examples.addEventListener('change', async () => {
    await saveSettings({ 'hub.loadExamples': examples.checked }, examples.checked ? '已載入內建範例' : '已隱藏內建範例');
    await nav.refreshExtensions();
  });
  return h('div', { class: 'stack' }, h('div', { class: 'row' }, '模式', mode), h('div', { class: 'row' }, '強調色', accent),
    h('label', { class: 'check' }, examples, '載入內建範例擴充（hello / 安全探針 / echo 後端）'),
    h('p', { class: 'muted small' }, '擴充會即時收到主題變更（xhub theme-changed 事件）。'));
}

function strategistForm(s) {
  const root = h('input', { type: 'text', value: s['strategist.root'] || '', style: { flex: '1' } });
  const gateway = h('input', { type: 'text', value: s['strategist.gatewayUrl'] || '', style: { flex: '1' } });
  const save = () => saveSettings({ 'strategist.root': root.value.trim(), 'strategist.gatewayUrl': gateway.value.trim() }, '已儲存');
  return h('div', { class: 'stack' }, h('div', { class: 'row' }, h('span', { class: 'small muted' }, '根目錄'), root),
    h('div', { class: 'row' }, h('span', { class: 'small muted' }, '閘道'), gateway),
    h('div', { class: 'row' }, h('button', { class: 'btn sm primary', onclick: save }, '儲存'),
      h('span', { class: 'muted small' }, '閘道只允許 127.0.0.1 / localhost')));
}

function backupBox() {
  const out = h('div', { class: 'small mono' });
  return h('div', { class: 'stack' }, kv([['資料根', store.state.dataRoot], ['Hub 版本', store.state.version]]),
    h('div', { class: 'row' }, h('button', { class: 'btn sm', onclick: async () => {
      const res = await guard(post('/api/backup'), '備份完成');
      out.textContent = res.path;
    } }, '建立備份 zip')), out,
    h('p', { class: 'muted small' }, '備份含資料庫與已安裝擴充；API key 在 macOS Keychain，不在備份內。還原請用：python3 -m strategist.hub restore <zip>（需先關閉 Hub）'));
}

async function doctorBox() {
  const box = h('div', { class: 'stack small' });
  try {
    const res = await get('/api/doctor');
    box.append(...res.checks.map((c) => h('div', { class: 'row' }, dot(c.ok), h('strong', {}, c.name), h('span', { class: 'muted' }, c.detail))));
  } catch (err) { box.append(h('div', { class: 'errbox' }, err.message)); }
  return box;
}

async function auditBox() {
  const res = await get('/api/audit?limit=50');
  if (!res.items.length) return h('p', { class: 'muted' }, '尚無紀錄');
  return h('table', { class: 'grid' }, h('tbody', {}, res.items.map((a) => h('tr', {},
    h('td', { class: 'small muted' }, timeAgo(a.ts)), h('td', { class: 'mono' }, a.actor),
    h('td', {}, badge(a.action, a.action === 'permission-denied' ? 'high' : '')), h('td', { class: 'small mono' }, a.detail)))));
}

function aboutBox() {
  return h('div', { class: 'stack small' },
    h('p', {}, 'Randy Hub：軍師中控的 macOS 桌面殼 + Extension Host。'),
    h('p', {}, '擴充契約、權限模型、橋 API、Bento 設計改寫自開源專案 x-hub（MIT License, Copyright (c) 2026 dckxx, github.com/dckxx/x-hub）。'),
    h('p', { class: 'muted' }, '完整聲明見 strategist/hub/THIRD_PARTY_NOTICES.md。'));
}

