import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SkillsRulesPanel } from './SkillsRulesPanel';

const jsonResponse = (body: unknown) => ({
  ok: true,
  headers: { get: () => 'application/json' },
  json: async () => body,
});

describe('SkillsRulesPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('loads rules and skills and previews resolution', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || 'GET').toUpperCase();

      if (method === 'GET' && url.includes('/instructions/rules')) {
        return jsonResponse([
          {
            id: 'r1',
            name: '默认安全协作规则',
            body: '高风险需授权',
            enabled: true,
            priority: 10,
            content_hash: 'abc12345deadbeef',
          },
        ]);
      }
      if (method === 'GET' && url.includes('/instructions/skills')) {
        return jsonResponse([
          {
            id: 's1',
            name: '代码评审',
            body: '四项检查',
            version: '1.0.0',
            published_version: '1.0.0',
            enabled: true,
            always_on: false,
            draft_dirty: true,
            trigger_keywords: ['评审'],
            content_hash: 'def67890cafebabe',
            versions: [{ version: '1.0.0', changelog: '初始' }],
            input_schema: {
              type: 'object',
              properties: { code: { type: 'string' } },
              required: ['code'],
            },
          },
        ]);
      }
      if (method === 'GET' && url.includes('/instructions/workflows')) {
        return jsonResponse([]);
      }
      if (method === 'POST' && url.includes('/publish')) {
        return jsonResponse({ id: 's1', published_version: '1.0.1', draft_dirty: false });
      }
      if (method === 'POST' && url.includes('/instructions/resolve')) {
        return jsonResponse({
          context: '### 规则：默认安全协作规则\n高风险需授权',
          layer_count: 1,
          manifest: [{ kind: 'rule', id: 'r1', name: '默认安全协作规则', content_hash: 'abc12345' }],
        });
      }
      if (method === 'POST' && url.includes('/instructions/dry-run')) {
        return jsonResponse({
          dry_run: true,
          executed: false,
          status: 'needs_review',
          layer_count: 1,
          layers: [{ kind: 'skill', name: '代码评审', version: '1.0.0' }],
          permissions: [{
            tool: 'run_command',
            label: '沙箱命令',
            risk: 'command_execution',
            level: 'high',
            requires_approval: true,
            note: '正文点名工具',
          }],
          summary: {
            permission_count: 1,
            high_risk_count: 1,
            approval_count: 1,
            io_field_count: 0,
          },
          issues: ['检测到 1 项高风险能力，执行前可能弹出授权卡片'],
        });
      }
      throw new Error(`unexpected ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<SkillsRulesPanel apiBase="http://127.0.0.1:8000" sessionId="sess-1" />);

    await waitFor(() => {
      expect(screen.getByText('默认安全协作规则')).toBeTruthy();
      // Skill name appears in list card and workflow skill <select> option
      expect(screen.getAllByText('代码评审').length).toBeGreaterThan(0);
      expect(screen.getAllByText('草稿未发布').length).toBeGreaterThan(0);
      expect(screen.getByText('typed IO')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: '解析预览' }));
    await waitFor(() => {
      expect(screen.getByText(/预览：将注入 1 层指令/)).toBeTruthy();
      expect(screen.getByText(/rule:默认安全协作规则/)).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Dry-run 权限清单' }));
    await waitFor(() => {
      expect(screen.getByText(/Dry-run · needs_review/)).toBeTruthy();
      expect(screen.getByText('沙箱命令')).toBeTruthy();
      expect(screen.getByText('需授权')).toBeTruthy();
    });

    const publishButtons = screen.getAllByRole('button', { name: '发布' });
    fireEvent.click(publishButtons[publishButtons.length - 1]);
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url).includes('/publish') && init?.method === 'POST',
        ),
      ).toBe(true);
    });
  });
});
