// 軍師中控頁（Randy 自有）：唯讀狀態 + 使用者明確送出的指令（經現行閘道 /api/strategist/command）。
import { get, post } from './api.js';
import { strategistBody } from './dashboard.js';
import { card, clear, confirmDialog, guard, h, setActions, setTitle } from './ui.js';

const TARGETS = ['', 'claude', 'nim', 'gemini', 'gpt', 'mlx', 'ollama', 'journal'];

export async function renderStrategist(view) {
  setTitle('軍師中控');
  const statusBox = h('div', {});
  setActions(h('button', { class: 'btn', onclick: () => refresh() }, '重新整理'));
  view.append(h('div', { class: 'bento' },
    h('div', { class: 'card', style: { gridColumn: 'span 6', gridRow: 'span 10' } }, h('div', { class: 'card-title' }, '狀態（唯讀）'), statusBox),
    commandCard()));

  async function refresh() {
    clear(statusBox).append(h('span', { class: 'muted' }, '讀取中…'));
    try {
      const status = await get('/api/strategist/status');
      clear(statusBox).append(strategistBody(status), entrypoints(status));
    } catch (err) {
      clear(statusBox).append(h('div', { class: 'errbox' }, err.message));
    }
  }
  await refresh();
}

function entrypoints(status) {
  const e = status.entrypoints || {};
  return h('div', { class: 'small muted', style: { marginTop: '10px' } },
    `根目錄：${status.root}（${status.rootExists ? '存在' : '找不到 strategist/'}）· `,
    `cao_entry.py ${e.cao_entry ? '✓' : '✗'} · web_gateway.py ${e.web_gateway ? '✓' : '✗'}`,
    h('br'), 'Hub 不會 import 或修改 Strategist；路由設定請用原本的 CLI / Menu Bar。');
}

function commandCard() {
  const text = h('textarea', { placeholder: '要交給軍師的任務（會真的執行，可能消耗額度）' });
  const target = h('select', {}, TARGETS.map((t) => h('option', { value: t }, t || '自動派工')));
  const out = h('pre', { class: 'reply', hidden: true });
  const send = async () => {
    if (!text.value.trim()) return;
    if (!(await confirmDialog('送出後軍師中控會實際執行這個任務（可能消耗額度），確定？', { okText: '送出' }))) return;
    out.hidden = false;
    out.textContent = '執行中…（閘道同步等待結果）';
    try {
      const res = await guard(post('/api/strategist/command', { command: text.value, target: target.value }));
      out.textContent = `${res.success ? '✓ 成功' : '✗ 失敗'} · target=${res.target || '—'} · type=${res.task_type || '—'}\n\n${res.reply || res.message || ''}`;
    } catch (err) {
      out.textContent = `失敗：${err.message}`;
    }
  };
  return h('div', { class: 'card', style: { gridColumn: 'span 6', gridRow: 'span 10' } },
    h('div', { class: 'card-title' }, '下指令（經 127.0.0.1 閘道 → cao_entry.run_task）'),
    h('div', { class: 'stack' }, text, h('div', { class: 'row' }, target, h('span', { class: 'spacer' }),
      h('button', { class: 'btn primary', onclick: send }, '送出')), out));
}
