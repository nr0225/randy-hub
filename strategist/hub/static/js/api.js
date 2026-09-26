// Hub API 用戶端（Randy 自有）。令牌只從 URL fragment 取得一次，存 sessionStorage，隨即從網址列抹掉。

const TOKEN_KEY = 'randyHub.token';
let token = '';

export function bootstrapToken() {
  const match = location.hash.match(/^#token=([A-Za-z0-9_-]{20,})/);
  if (match) {
    token = match[1];
    try { sessionStorage.setItem(TOKEN_KEY, token); } catch (e) { /* 私密模式：只放記憶體 */ }
    history.replaceState(null, '', '/#/dashboard');
    return true;
  }
  try { token = sessionStorage.getItem(TOKEN_KEY) || ''; } catch (e) { token = ''; }
  return Boolean(token);
}

export class ApiError extends Error {
  constructor(status, body) {
    super((body && body.message) || `HTTP ${status}`);
    this.status = status;
    this.code = body && body.code;
    this.body = body;
  }
}

export async function api(method, path, body) {
  const init = { method, headers: { 'X-Randy-Hub-Token': token } };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(path, init);
  let data = null;
  try { data = await resp.json(); } catch (e) { data = null; }
  if (resp.status === 401) window.dispatchEvent(new CustomEvent('hub-unauthorized'));
  if (!resp.ok) throw new ApiError(resp.status, data);
  return data;
}

export const get = (path) => api('GET', path);
export const post = (path, body = {}) => api('POST', path, body);
export const put = (path, body = {}) => api('PUT', path, body);
export const del = (path, body) => api('DELETE', path, body);

export function bridgeCall(extId, namespace, method, args) {
  return post(`/api/ext/${encodeURIComponent(extId)}/call`, { namespace, method, args: args || {} });
}
