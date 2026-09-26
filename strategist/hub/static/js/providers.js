// AI 供應商（Hub 自己的，與軍師中控的模型路由解耦）。
// UX Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — src/components/AiProviders.vue：
// 測試連通、拉取模型勾選加入、key 存鑰匙圈、介面遮罩。Randy 修改：不提供「顯示 / 複製 key」。
import { del, get, post, put } from './api.js';
import { badge, card, clear, confirmDialog, dot, guard, h, setActions, setTitle, timeAgo } from './ui.js';

export async function renderProviders(view) {
  setTitle('AI 供應商');
  const list = h('div', { class: 'ext-list' });
  const note = h('div', { class: 'warnbox', style: { marginBottom: '14px' } },
    '這裡是 Randy Hub 自己的供應商（OpenAI 相容介面：GPT / Claude / NIM / DeepSeek / Ollama / MLX）。',
    '它不會改動軍師中控（Strategist）的模型路由；Strategist 仍用自己的設定，請看「軍師中控」頁。');
  const presetSelect = h('select', {});
  setActions(presetSelect, h('button', { class: 'btn primary', onclick: async () => {
    await guard(post('/api/providers', { preset: presetSelect.value }), '已新增');
    await refresh();
  } }, '新增供應商'));
  view.append(note, list);

  async function refresh() {
    const data = await get('/api/providers');
    if (!presetSelect.options.length) presetSelect.append(...data.presets.map((p) => h('option', { value: p.id }, p.name)));
    clear(list).append(...data.items.map((p) => providerCard(p, refresh)));
    if (!data.items.length) list.append(h('p', { class: 'muted' }, '還沒有供應商。右上角選一個預設組新增。'));
  }
  await refresh();
}

function providerCard(p, refresh) {
  const id = encodeURIComponent(p.id);
  const result = h('div', { class: 'stack' });
  const test = p.lastTest;
  const status = h('div', { class: 'row small' }, dot(test ? test.ok : null),
    test ? (test.ok ? `連線正常，${test.modelCount} 個模型（${timeAgo(test.at)}）` : `${test.error}（${timeAgo(test.at)}）`) : '尚未測試');
  const body = h('div', { class: 'stack' },
    h('div', { class: 'row' }, h('strong', {}, p.name), h('span', { class: 'spacer' }), badge(p.preset),
      p.needsKey ? badge(p.hasKey ? `key ${p.keyMasked}` : '缺 key', p.hasKey ? 'low' : 'high') : badge('本機，不需 key')),
    baseUrlRow(p, refresh), keyRow(p, refresh), status,
    h('div', { class: 'row' },
      h('button', { class: 'btn sm', onclick: async () => { await guard(post(`/api/providers/${id}/test`)); await refresh(); } }, '測試連線'),
      h('button', { class: 'btn sm', onclick: () => pickModels(p, result, refresh) }, '拉取模型'),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn sm danger', onclick: async () => {
        if (!(await confirmDialog(`刪除「${p.name}」？（Keychain 裡的 key 也會一起刪）`, { okText: '刪除', danger: true }))) return;
        await guard(del(`/api/providers/${id}`), '已刪除');
        await refresh();
      } }, '刪除')),
    modelRow(p, refresh), chatRow(p), result);
  return card(null, body);
}

function baseUrlRow(p, refresh) {
  const input = h('input', { type: 'url', value: p.baseUrl, style: { flex: '1' } });
  return h('div', { class: 'row' }, h('span', { class: 'muted small' }, 'Base URL'), input,
    h('button', { class: 'btn sm', onclick: async () => {
      await guard(put(`/api/providers/${encodeURIComponent(p.id)}`, { baseUrl: input.value }), '已更新');
      await refresh();
    } }, '儲存'));
}

function keyRow(p, refresh) {
  const id = encodeURIComponent(p.id);
  const input = h('input', { type: 'password', placeholder: p.hasKey ? '輸入新 key 以取代' : '貼上 API key（只存 macOS Keychain）', style: { flex: '1' } });
  return h('div', { class: 'row' }, h('span', { class: 'muted small' }, 'API key'), input,
    h('button', { class: 'btn sm', onclick: async () => {
      await guard(put(`/api/providers/${id}/key`, { key: input.value }), '已存入 Keychain');
      input.value = '';
      await refresh();
    } }, '儲存'),
    p.hasKey ? h('button', { class: 'btn sm danger', onclick: async () => { await guard(del(`/api/providers/${id}/key`)); await refresh(); } }, '清除') : null);
}

function modelRow(p, refresh) {
  if (!p.models.length) return h('div', { class: 'muted small' }, '尚未加入模型（按「拉取模型」勾選）');
  const select = h('select', {}, p.models.map((m) => h('option', { value: m, selected: m === p.defaultModel }, m)));
  select.addEventListener('change', async () => {
    await guard(put(`/api/providers/${encodeURIComponent(p.id)}`, { defaultModel: select.value }), '預設模型已更新');
    await refresh();
  });
  if (!p.defaultModel && p.models.length) select.value = '';
  return h('div', { class: 'row' }, h('span', { class: 'muted small' }, '預設模型'), select);
}

async function pickModels(p, box, refresh) {
  clear(box).append(h('span', { class: 'muted small' }, '拉取中…'));
  let models = [];
  try { models = (await get(`/api/providers/${encodeURIComponent(p.id)}/models`)).models; } catch (err) {
    clear(box).append(h('div', { class: 'errbox' }, err.message));
    return;
  }
  const chosen = new Set(p.models);
  const boxes = models.map((m) => h('label', { class: 'check small' }, h('input', { type: 'checkbox', checked: chosen.has(m),
    onchange: (e) => (e.target.checked ? chosen.add(m) : chosen.delete(m)) }), m));
  clear(box).append(h('div', { class: 'stack', style: { maxHeight: '220px', overflow: 'auto' } }, boxes),
    h('button', { class: 'btn sm primary', onclick: async () => {
      const list = [...chosen];
      const fields = { models: list };
      if (!p.defaultModel && list.length) fields.defaultModel = list[0];
      await guard(put(`/api/providers/${encodeURIComponent(p.id)}`, fields), `已加入 ${list.length} 個模型`);
      await refresh();
    } }, '儲存勾選'));
}

function chatRow(p) {
  const input = h('input', { type: 'text', placeholder: '試打一句（用預設模型，會消耗額度）', style: { flex: '1' } });
  const out = h('pre', { class: 'reply', hidden: true });
  const send = async () => {
    out.hidden = false;
    out.textContent = '等待回覆…';
    try {
      const res = await post(`/api/providers/${encodeURIComponent(p.id)}/chat`, { prompt: input.value });
      out.textContent = `[${res.model} · ${res.latencyMs} ms]\n${res.reply}`;
    } catch (err) { out.textContent = `失敗：${err.message}`; }
  };
  return h('div', { class: 'stack' }, h('div', { class: 'row' }, input, h('button', { class: 'btn sm', disabled: !p.defaultModel, onclick: send }, '送出')), out);
}
