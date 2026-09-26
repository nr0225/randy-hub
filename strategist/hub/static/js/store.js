// 前端共用狀態（Randy 自有）。頁面模組只從這裡拿狀態與導覽函式，避免循環 import。
export const store = { state: null, extensions: [], extensionErrors: [], mounts: [], cleanup: [] };

export function onLeave(fn) {
  store.cleanup.push(fn);
}

export function runCleanup() {
  for (const fn of store.cleanup.splice(0)) {
    try { fn(); } catch (e) { /* 單一清理失敗不擋換頁 */ }
  }
}

// 由 main.js 在啟動時填入
export const nav = {
  openSurface: () => {},
  refreshExtensions: async () => [],
  reloadState: async () => null,
};
