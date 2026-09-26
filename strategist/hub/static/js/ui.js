// DOM 小工具（Randy 自有）。所有外部資料（manifest 名稱、描述、回覆）一律走 textContent，不用 innerHTML。

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (value === true) el.setAttribute(key, '');
    else el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

export function toast(message, kind = 'info') {
  const box = h('div', { class: `toast ${kind}` }, message);
  document.getElementById('toasts').append(box);
  setTimeout(() => box.remove(), kind === 'error' ? 7000 : 3500);
}

// 頁內確認框：WKWebView 沒有 UI delegate 時 window.confirm() 會直接回 false，所以不用它。
export function confirmDialog(message, { okText = '確定', danger = false } = {}) {
  return new Promise((resolve) => {
    const close = (value) => { overlay.remove(); resolve(value); };
    const ok = h('button', { class: `btn ${danger ? 'danger' : 'primary'}`, onclick: () => close(true) }, okText);
    const overlay = h('div', { class: 'modal-scrim', onclick: (e) => { if (e.target === overlay) close(false); } },
      h('div', { class: 'card modal', role: 'dialog', 'aria-modal': 'true' },
        h('p', { style: { whiteSpace: 'pre-wrap' } }, message),
        h('div', { class: 'row', style: { justifyContent: 'flex-end' } },
          h('button', { class: 'btn', onclick: () => close(false) }, '取消'), ok)));
    overlay.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(false); });
    document.body.append(overlay);
    ok.focus();
  });
}

export function dot(state) {
  return h('span', { class: `dot ${state === true ? 'ok' : state === false ? 'bad' : 'warn'}` });
}

export function badge(text, kind = '') {
  return h('span', { class: `badge ${kind}` }, text);
}

export function kv(rows) {
  const dl = h('dl', { class: 'kv' });
  for (const [k, v] of rows) dl.append(h('dt', {}, k), h('dd', {}, v ?? '—'));
  return dl;
}

export function card(title, body, opts = {}) {
  return h('div', { class: `card ${opts.class || ''}`, style: opts.style },
    title ? h('div', { class: 'card-title' }, title, h('span', { class: 'spacer' }), opts.actions || null) : null,
    h('div', { class: 'card-body' }, body));
}

export function setActions(...nodes) {
  clear(document.getElementById('page-actions')).append(...nodes.filter(Boolean));
}

export function setTitle(text) {
  document.getElementById('page-title').textContent = text;
  document.title = `${text} · Randy Hub`;
}

export async function guard(promise, okMessage) {
  try {
    const result = await promise;
    if (okMessage) toast(okMessage);
    return result;
  } catch (err) {
    toast(err.message || String(err), 'error');
    throw err;
  }
}

export function timeAgo(ts) {
  if (!ts) return '—';
  const diff = Math.max(0, Date.now() / 1000 - ts);
  if (diff < 60) return `${Math.round(diff)} 秒前`;
  if (diff < 3600) return `${Math.round(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 小時前`;
  return new Date(ts * 1000).toLocaleString('zh-TW');
}
