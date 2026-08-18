import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ChatEmptyState } from './ChatEmptyState';

describe('ChatEmptyState', () => {
  it('renders executable examples and fills composer when clicked', () => {
    const onPickExample = vi.fn();
    render(
      <ChatEmptyState
        hasConfiguredModel
        onPickExample={onPickExample}
      />,
    );

    expect(screen.getByText('快速认识工作台')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /快速认识工作台/ }));
    expect(onPickExample).toHaveBeenCalledWith(
      expect.stringContaining('用三句话介绍你能帮我做什么'),
    );
  });

  it('disables examples when no model is configured', () => {
    const onPickExample = vi.fn();
    render(
      <ChatEmptyState
        hasConfiguredModel={false}
        onPickExample={onPickExample}
      />,
    );
    const button = screen.getByRole('button', { name: /拆解一个小任务/ });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onPickExample).not.toHaveBeenCalled();
    expect(screen.getByText(/还没有可用模型/)).toBeTruthy();
  });

  it('hints local model start path when local config exists', () => {
    render(
      <ChatEmptyState
        hasConfiguredModel
        hasLocalModel
        onPickExample={vi.fn()}
      />,
    );
    expect(screen.getByText(/启动并验证/)).toBeTruthy();
    expect(screen.getByText('用本地模型打个招呼')).toBeTruthy();
  });
});

