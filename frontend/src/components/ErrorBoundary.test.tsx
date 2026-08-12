import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ErrorBoundary } from './ErrorBoundary';

function GoodChild() {
  return <p>正常内容</p>;
}

let shouldThrow = false;
function MaybeThrow() {
  if (shouldThrow) {
    throw new Error('boom');
  }
  return <p>正常内容</p>;
}

describe('ErrorBoundary', () => {
  beforeEach(() => {
    shouldThrow = false;
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('无错误时正常渲染子组件', () => {
    render(
      <ErrorBoundary scope="聊天区">
        <GoodChild />
      </ErrorBoundary>,
    );
    expect(screen.getByText('正常内容')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('捕获渲染错误并显示回退 UI 与诊断码', () => {
    shouldThrow = true;
    render(
      <ErrorBoundary scope="聊天区">
        <MaybeThrow />
      </ErrorBoundary>,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('聊天区暂时不可用');
    expect(alert.querySelector('code')!.textContent).toMatch(/^ui-/);
    expect(screen.queryByText('正常内容')).toBeNull();
    expect(console.error).toHaveBeenCalled();
  });

  it('错误带 requestId 时诊断码使用该 requestId', () => {
    function ThrowWithRequestId(): never {
      const error = new Error('with-request-id') as Error & { requestId?: string };
      error.requestId = 'req-abc-123';
      throw error;
    }
    render(
      <ErrorBoundary scope="任务区">
        <ThrowWithRequestId />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert').querySelector('code')).toHaveTextContent('req-abc-123');
  });

  it('点击“重试此区域”后恢复渲染子组件', () => {
    shouldThrow = true;
    render(
      <ErrorBoundary scope="聊天区">
        <MaybeThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
    shouldThrow = false;
    fireEvent.click(screen.getByRole('button', { name: '重试此区域' }));
    expect(screen.getByText('正常内容')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('点击“复制诊断码”将诊断码写入剪贴板', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    shouldThrow = true;
    render(
      <ErrorBoundary scope="聊天区">
        <MaybeThrow />
      </ErrorBoundary>,
    );
    const diagnosticId = screen.getByRole('alert').querySelector('code')!.textContent!;
    fireEvent.click(screen.getByRole('button', { name: '复制诊断码' }));
    await screen.findByRole('button', { name: '已复制' });
    expect(writeText).toHaveBeenCalledWith(diagnosticId);
  });

  it('resetKey 变化时自动重置错误状态', () => {
    shouldThrow = true;
    const { rerender } = render(
      <ErrorBoundary scope="聊天区" resetKey="a">
        <MaybeThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
    shouldThrow = false;
    rerender(
      <ErrorBoundary scope="聊天区" resetKey="b">
        <MaybeThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByText('正常内容')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
