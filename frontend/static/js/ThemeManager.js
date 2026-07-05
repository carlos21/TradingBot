// ThemeManager.js - client-side dark / light theme switcher

(function () {
  'use strict';

  const STORAGE_KEY = 'liquid-theme';

  const THEMES = {
    dark: {
      name: 'Dark',
      surface: {
        950: '11 18 32',
        900: '15 23 42',
        850: '21 31 53',
        800: '30 41 59',
        700: '51 65 85',
        600: '71 85 105',
      },
      slate: {
        50: '248 250 252',
        100: '241 245 249',
        200: '226 232 240',
        300: '203 213 225',
        400: '148 163 184',
        500: '100 116 139',
        600: '71 85 105',
        700: '51 65 85',
        800: '30 41 59',
        900: '15 23 42',
      },
    },
    light: {
      name: 'Light',
      surface: {
        950: '255 255 255',
        900: '248 250 252',
        850: '241 245 249',
        800: '241 245 249',
        700: '226 232 240',
        600: '203 213 225',
      },
      slate: {
        50: '15 23 42',
        100: '30 41 59',
        200: '51 65 85',
        300: '71 85 105',
        400: '148 163 184',
        500: '203 213 225',
        600: '226 232 240',
        700: '241 245 249',
        800: '248 250 252',
        900: '255 255 255',
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
