import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ApprovalRequest } from '../types';
import { ApprovalPrompt } from './ApprovalPrompt';

function makeApproval(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    id: 'approval-1',
    session_id: 'session-1',
    model: 'gpt-test',
    tool_name: 'shell_exec',
    risk: 'high',
    title: '执行删除文件命令',
    detail: 'rm -rf /tmp/x',
    arguments: { path: '/tmp/x' },
    allow_session: true,
    created_at: '2026-08-12T00:00:00Z',
    ...overrides,
  };
}

describe('ApprovalPrompt', () => {
  it('渲染标题、模型/工具、风险与排队信息', () => {
    render(
      <ApprovalPrompt
        approval={makeApproval()}
        queuedCount={2}
        onDecision={vi.fn()}
      />,
    );
    expect(screen.getByText('执行删除文件命令')).toBeInTheDocument();
    expect(screen.getByText('gpt-test 请求调用 shell_exec')).toBeInTheDocument();
    expect(screen.getByText('风险：high')).toBeInTheDocument();
    expect(screen.getByText('另有 2 个授权请求排队')).toBeInTheDocument();
    expect(screen.getByText('rm -rf /tmp/x')).toBeInTheDocument();
  });

  it('allow_session 为 false 时不显示会话授权选项', () => {
    render(
      <ApprovalPrompt approval={makeApproval({ allow_session: false })} onDecision={vi.fn()} />,
    );
    expect(screen.getByText('是，仅允许这一次')).toBeInTheDocument();
    expect(screen.queryByText(/不再询问/)).toBeNull();
  });

  it('点击“仅允许这一次”触发 allow_once 回调', async () => {
    const onDecision = vi.fn().mockResolvedValue(undefined);
    const approval = makeApproval();
    render(<ApprovalPrompt approval={approval} onDecision={onDecision} />);
    fireEvent.click(screen.getByText('是，仅允许这一次'));
    await waitFor(() => {
      expect(onDecision).toHaveBeenCalledWith(approval, 'allow_once', undefined);
    });
  });

  it('点击会话授权选项触发 allow_session 回调', async () => {
    const onDecision = vi.fn().mockResolvedValue(undefined);
    const approval = makeApproval();
    render(<ApprovalPrompt approval={approval} onDecision={onDecision} />);
    fireEvent.click(screen.getByText(/并在本会话中不再询问 shell_exec/));
    await waitFor(() => {
      expect(onDecision).toHaveBeenCalledWith(approval, 'allow_session', undefined);
    });
  });

  it('选择拒绝后进入反馈模式，提交反馈触发 deny 回调', async () => {
    const onDecision = vi.fn().mockResolvedValue(undefined);
    const approval = makeApproval();
    render(<ApprovalPrompt approval={approval} onDecision={onDecision} />);
    fireEvent.click(screen.getByText('否，并告诉 AetherChat 应如何调整'));
    // 尚未提交
    expect(onDecision).not.toHaveBeenCalled();
    const input = screen.getByPlaceholderText('输入反馈；留空将直接拒绝');
    fireEvent.change(input, { target: { value: '  请先列出将删除的文件  ' } });
    fireEvent.click(screen.getByText('拒绝并发送'));
    await waitFor(() => {
      expect(onDecision).toHaveBeenCalledWith(approval, 'deny', '请先列出将删除的文件');
    });
  });

  it('拒绝反馈为空时回调携带空字符串反馈', async () => {
    const onDecision = vi.fn().mockResolvedValue(undefined);
    const approval = makeApproval();
    render(<ApprovalPrompt approval={approval} onDecision={onDecision} />);
    fireEvent.click(screen.getByText('否，并告诉 AetherChat 应如何调整'));
    fireEvent.click(screen.getByText('拒绝并发送'));
    await waitFor(() => {
      expect(onDecision).toHaveBeenCalledWith(approval, 'deny', '');
    });
  });

  it('键盘操作：方向键选择 + Enter 确认', async () => {
    const onDecision = vi.fn().mockResolvedValue(undefined);
    const approval = makeApproval();
    render(<ApprovalPrompt approval={approval} onDecision={onDecision} />);
    const listbox = screen.getByRole('listbox');
    fireEvent.keyDown(listbox, { key: 'ArrowDown' });
    fireEvent.keyDown(listbox, { key: 'ArrowUp' });
    fireEvent.keyDown(listbox, { key: 'Enter' });
    await waitFor(() => {
      expect(onDecision).toHaveBeenCalledWith(approval, 'allow_once', undefined);
    });
  });

  it('反馈模式下按返回回到选项菜单', () => {
    render(<ApprovalPrompt approval={makeApproval()} onDecision={vi.fn()} />);
    fireEvent.click(screen.getByText('否，并告诉 AetherChat 应如何调整'));
    expect(screen.getByPlaceholderText('输入反馈；留空将直接拒绝')).toBeInTheDocument();
    fireEvent.click(screen.getByText('返回'));
    expect(screen.getByText('请选择如何继续：')).toBeInTheDocument();
  });
});
