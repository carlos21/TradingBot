import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'happy-dom',
    globals: false,
    include: ['static/js/**/*.test.js'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['static/js/**/*.js'],
      exclude: [
        'static/js/**/*.test.js',
        'static/js/tests/fakes/**',
        'static/js/main.js',
        'static/js/admin/AdminApp.js',
        'static/js/composition/**',
      ],
    },
  },
});
