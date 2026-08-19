import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WorkspaceSidebar } from './chrome';

const conversations = [
  {
    id: 's1',
    title: '雨夜出图',
    summary: '这行摘要不应再出现在侧栏',
    updatedAt: '昨天',
    projectId: 'p1',
  },
  {
    id: 's2',
    title: '冒烟项目DAG-planned',
    summary: '诊断会话',
    updatedAt: '昨天',
    projectId: 'p1',
  },
];

describe('WorkspaceSidebar', () => {
  it('uses compact new-chat / new-project actions and a title-only recents list', () => {
    const onNewConversation = vi.fn();
    const onNewProject = vi.fn();
    render(
      <WorkspaceSidebar
        conversations={conversations as never}
        currentSessionId="s1"
        projects={[{ id: 'p1', name: '封面项目' }]}
        activeProjectId="p1"
        onNewConversation={onNewConversation}
        onNewProject={onNewProject}
        onSelectProject={vi.fn()}
        onOpenSearch={vi.fn()}
        onSelectConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '新建会话' })).toBeTruthy();
    expect(screen.getByText('新建对话')).toBeTruthy();
    expect(screen.getByRole('button', { name: '新建项目' })).toBeTruthy();
    expect(screen.getByText('封面项目')).toBeTruthy();
    expect(screen.getByRole('button', { name: '雨夜出图' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '冒烟项目DAG-planned' })).toBeNull();
    expect(screen.getByRole('button', { name: /显示诊断会话/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /显示诊断会话/ }));
    expect(screen.getByRole('button', { name: '冒烟项目DAG-planned' })).toBeTruthy();
    expect(screen.queryByText('这行摘要不应再出现在侧栏')).toBeNull();
    expect(screen.queryByText('aetherchat/main')).toBeNull();
    expect(screen.queryByText('多模型 Agent 工作台')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    expect(onNewConversation).toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '新建项目' }));
    fireEvent.change(screen.getByLabelText('项目名称'), { target: { value: '新封面' } });
    fireEvent.submit(screen.getByLabelText('项目名称').closest('form') as HTMLFormElement);
    expect(onNewProject).toHaveBeenCalledWith('新封面');
  });
});
