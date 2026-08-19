import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useSessionStore } from '../stores/sessionStore';
import type { Message, ReasoningStep } from '../types';
import { UnifiedFeed } from './UnifiedFeed';


const assistantMessage = (steps: ReasoningStep[]): Message => ({
  id: 'message-1',
  session_id: '11111111-1111-4111-8111-111111111111',
  author_model: 'Qwen',
  role: 'assistant',
  content: '这是最终答复。',
  created_at: '2026-08-13T00:00:00Z',
  reasoning_summary: steps,
});


const renderFeed = (steps: ReasoningStep[]) => {
  useSessionStore.setState((state) => ({
    messages: [assistantMessage(steps)],
    settings: { ...state.settings, uiLocale: 'zh-CN' },
  }));
  return render(<UnifiedFeed />);
};


afterEach(() => {
  cleanup();
  useSessionStore.setState({ messages: [] });
});


describe('UnifiedFeed generated files', () => {
  it('shows human file names and hides metadata json cards', () => {
    useSessionStore.setState((state) => ({
      messages: [{
        ...assistantMessage([]),
        generated_files: [
          { name: 'cover.png', path: '/tmp/cover.png', mime_type: 'image/png', size_bytes: 12 },
          { name: 'cover.metadata.json', path: '/tmp/cover.metadata.json', mime_type: 'application/json' },
        ],
      }],
      settings: { ...state.settings, uiLocale: 'zh-CN' },
    }));
    render(<UnifiedFeed />);
    expect(screen.getByText('cover.png')).toBeTruthy();
    expect(screen.queryByLabelText('预览文件 cover.metadata.json')).toBeNull();
    expect(screen.getByText('技术细节')).toBeTruthy();
    expect(screen.getByText('已保存到本机')).toBeTruthy();
    expect(screen.queryByText(/\/tmp\/cover\.png/)).toBeNull();
  });
});

describe('UnifiedFeed execution receipt', () => {
  it('does not turn a final model step into a verified completion', () => {
    renderFeed([{ phase: 'final', detail: '最终答复已生成' }]);

    expect(screen.getByText('回答已生成，尚未获得外部执行核验')).toBeInTheDocument();
    expect(screen.queryByText('本次处理已完成')).toBeNull();
    expect(screen.getByLabelText('实际执行与交付')).toHaveAttribute(
      'data-execution-status',
      'unverified',
    );
  });

  it('keeps failed tool evidence out of the green state', () => {
    renderFeed([
      { phase: 'tool', detail: '已选择调用 workspace_write_file' },
      { phase: 'result', detail: '工具 workspace_write_file 执行失败' },
      { phase: 'final', detail: '最终答复已生成' },
    ]);

    expect(screen.getByText('执行已结束，存在需检查的结果')).toBeInTheDocument();
    expect(screen.getByLabelText('实际执行与交付')).toHaveAttribute(
      'data-execution-status',
      'failed',
    );
  });

  it('shows verified only for a trusted desktop execution receipt', () => {
    renderFeed([
      { phase: 'tool', detail: '已选择调用 plugin__browser_links__open_url' },
      {
        phase: 'result',
        detail: '工具 plugin__browser_links__open_url：桌面启动器已确认 launcher_accepted execution_receipt',
      },
      { phase: 'final', detail: '最终答复已生成' },
    ]);

    expect(screen.getByText('外部操作已核验完成')).toBeInTheDocument();
    expect(screen.getByLabelText('实际执行与交付')).toHaveAttribute(
      'data-execution-status',
      'verified',
    );
  });
});
