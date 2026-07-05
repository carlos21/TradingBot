import { createAdminApp } from '../composition/adminApp.js';

// Global error handling
window.addEventListener('error', (e) => {
  console.error('[Global Error]', e.message, e.filename, e.lineno);
});

window.addEventListener('unhandledrejection', (e) => {
  console.error('[Unhandled Promise Rejection]', e.reason);
});

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  console.log('[AdminApp] DOM ready, initializing app...');
  const { controller } = createAdminApp();
  controller.init().catch(err => {
    console.error('[AdminApp] Init failed:', err);
  });
});
