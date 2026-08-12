import { coverageConfigDefaults, defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
    coverage: {
      provider: 'v8',
      include: ['src/**'],
      exclude: [...coverageConfigDefaults.exclude, 'src/**/*.bak'],
      reporter: ['text', 'json-summary'],
      // 仅对已有测试安全网的文件设阈值；其余文件不限制，避免全局高阈值阻塞。
      thresholds: {
        'src/components/SafeMarkdown.tsx': {
          lines: 90,
          functions: 75,
          branches: 80,
          statements: 90,
        },
        'src/components/ErrorBoundary.tsx': {
          lines: 70,
          functions: 70,
          branches: 60,
          statements: 70,
        },
        'src/components/ApprovalPrompt.tsx': {
          lines: 70,
          functions: 60,
          branches: 60,
          statements: 70,
        },
      },
    },
  },
});
