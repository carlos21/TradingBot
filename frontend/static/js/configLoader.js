/**
 * Loads runtime config from /api/config, exposes it on window.TRADINGBOT_CONFIG,
 * and toggles platform-specific DOM elements.
 */
(function () {
  function applyConfig(config) {
    window.TRADINGBOT_CONFIG = config;

    // Set platform label text where requested and update any data-platform-label attrs.
    document.querySelectorAll('[data-config-key="platform_label"]').forEach((el) => {
      el.textContent = config.platform_label || config.platform_type || '';
    });
    document.querySelectorAll('[data-platform-label]').forEach((el) => {
      el.dataset.platformLabel = config.platform_label || config.platform_type || '';
    });

    // Show/hide elements tied to a specific platform.
    document.querySelectorAll('[data-platform]').forEach((el) => {
      const platform = el.getAttribute('data-platform');
      const visible =
        (platform === 'ninjatrader' && config.is_ninjatrader) ||
        (platform === 'metatrader' && config.is_metatrader);
      if (visible) {
        el.classList.remove('hidden');
      } else {
        el.classList.add('hidden');
      }
    });

    window.dispatchEvent(new CustomEvent('tradingbot-config-loaded', { detail: config }));
  }

  fetch('/api/config')
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error('config fetch failed'))))
    .then(applyConfig)
    .catch((err) => {
      console.error('[configLoader] Failed to load config:', err);
    });
})();
