// 主題：亮 / 暗 / 跟隨系統 + 強調色；並把目前令牌整理成 x-hub 相容的 theme 物件給擴充。
// 令牌對照表 Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — src/composables/themeTokens.ts 的欄位命名。

let current = { mode: 'system', accent: '#5b5bf5' };
const listeners = new Set();
const media = window.matchMedia('(prefers-color-scheme: dark)');

export function resolvedMode() {
  return current.mode === 'system' ? (media.matches ? 'dark' : 'light') : current.mode;
}

export function applyTheme(settings) {
  current = { mode: settings['theme.mode'] || 'system', accent: settings['theme.accent'] || '' };
  const root = document.documentElement;
  root.dataset.theme = resolvedMode();
  if (current.accent) root.style.setProperty('--accent', current.accent);
  else root.style.removeProperty('--accent');
  listeners.forEach((fn) => fn(collectTheme()));
}

export function onThemeChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

media.addEventListener('change', () => {
  if (current.mode === 'system') applyTheme({ 'theme.mode': 'system', 'theme.accent': current.accent });
});

export function collectTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return {
    mode: resolvedMode(),
    preset: 'default',
    accent: v('--accent'),
    wallpaper: { on: false, clear: false, immersive: false },
    tokens: {
      accent: v('--accent'), brand: v('--accent'), brandSoft: v('--brand-50'),
      bgPage: v('--bg-page-surface'), pageBg: 'transparent', bgCard: v('--bg-card-solid'),
      surface: v('--frost-surface'), text1: v('--text-1'), text2: v('--text-2'), text3: v('--text-3'),
      border: v('--border-soft'), red: v('--c-red'), green: v('--c-green'), yellow: v('--c-yellow'),
      blue: v('--c-blue'), orange: v('--c-orange'), radiusLg: v('--radius-lg'),
    },
  };
}
