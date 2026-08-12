import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StartupGate } from './StartupGate';

describe('StartupGate', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it('renders the application only after the desktop backend is ready', () => {
    const { rerender } = render(
      <StartupGate runtime={{ backendStatus: 'starting' }}><p>工作台内容</p></StartupGate>,
    );
    expect(screen.getByRole('status')).toHaveTextContent('正在检查本机服务');
    expect(screen.queryByText('工作台内容')).not.toBeInTheDocument();

    rerender(
      <StartupGate runtime={{ backendStatus: 'ready' }}><p>工作台内容</p></StartupGate>,
    );
    expect(screen.getByText('工作台内容')).toBeVisible();
  });

  it('shows safe schema recovery guidance, copies diagnostics and retries', async () => {
    const retry = vi.fn();
    render(
      <StartupGate
        runtime={{
          backendStatus: 'unavailable',
          backendErrorCode: 'database_schema_mismatch',
          backendRequestId: 'request-schema-42',
        }}
        onRetry={retry}
      >
        <p>工作台内容</p>
      </StartupGate>,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('本机数据库版本不兼容');
    expect(screen.getByRole('alert')).toHaveTextContent('请不要删除用户数据');
    expect(screen.getByRole('alert')).toHaveTextContent('request-schema-42');
    expect(screen.queryByText('工作台内容')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '复制诊断 ID' }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('request-schema-42'));
    fireEvent.click(screen.getByRole('button', { name: '重试启动' }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
