// ThemeManager.js - client-side dark / light theme switcher

(function () {
  'use strict';

  const STORAGE_KEY = 'liquid-theme';

  const THEMES = {
    dark: {
      name: 'Dark',
      surface: {
        950: '#0b1220',
        900: '#0f172a',
        850: '#151f35',
        800: '#1e293b',
        700: '#334155',
        600: '#475569',
      },
      slate: {
        50: '#f8fafc',
        100: '#f1f5f9',
        200: '#e2e8f0',
        300: '#cbd5e1',
        400: '#94a3b8',
        500: '#64748b',
        600: '#475569',
        700: '#334155',
        800: '#1e293b',
        900: '#0f172a',
      },
    },
    light: {
      name: 'Light',
      surface: {
        950: '#ffffff',
        900: '#f8fafc',
        850: '#f1f5f9',
        800: '#f1f5f9',
        700: '#e2e8f0',
        600: '#cbd5e1',
      },
      slate: {
        50: '#0f172a',
        100: '#1e293b',
        200: '#334155',
        300: '#475569',
        400: '#94a3b8',
        500: '#cbd5e1',
        600: '#e2e8f0',
        700: '#f1f5f9',
        800: '#f8fafc',
        900: '#ffffff',
      },
    },
  };

  function applyTheme(themeKey) {
    const theme = THEMES[themeKey] || THEMES.dark;
    const root = document.documentElement;

    Object.entries(theme.surface).forEach(([shade, color]) => {
      root.style.setProperty(`--surface-${shade}`, color);
    });
    Object.entries(theme.slate).forEach(([shade, color]) => {
      root.style.setProperty(`--slate-${shade}`, color);
    });

    try {
      localStorage.setItem(STORAGE_KEY, themeKey);
    } catch (e) {
      // ignore storage errors
    }

    const selector = document.getElementById('theme-selector');
    if (selector) {
      selector.value = themeKey;
    }
  }

  function init() {
    let saved;
    try {
      saved = localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      saved = null;
    }
    applyTheme(saved && THEMES[saved] ? saved : 'dark');

    const selector = document.getElementById('theme-selector');
    if (selector) {
      selector.addEventListener('change', (e) => {
        applyTheme(e.target.value);
      });
    }
  }

  // Expose a tiny API for other scripts
  window.LiquidTheme = {
    apply: applyTheme,
    list: THEMES,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
