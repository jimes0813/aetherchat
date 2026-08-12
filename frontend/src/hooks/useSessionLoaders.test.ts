import { describe, expect, it } from 'vitest';
import { conversationFromSession, type StoredSession } from './useSessionLoaders';

describe('conversationFromSession', () => {
  it('maps project_id to projectId', () => {
    const session: StoredSession = {
      id: 's1',
      title: '测试',
      summary: '摘要',
      mode: 'project',
      project_id: 'default',
      created_at: '2026-08-13T00:00:00Z',
      updated_at: '2026-08-13T00:00:00Z',
      message_count: 2,
      task_count: 1,
      open_task_count: 0,
    };
    const conversation = conversationFromSession(session);
    expect(conversation.projectId).toBe('default');
    expect(conversation.mode).toBe('project');
    expect(conversation.messageCount).toBe(2);
  });

  it('uses null projectId when unbound', () => {
    const conversation = conversationFromSession({
      id: 's2',
      title: '空',
      summary: '',
      mode: 'casual',
      created_at: '2026-08-13T00:00:00Z',
      updated_at: '2026-08-13T00:00:00Z',
      message_count: 0,
      task_count: 0,
      open_task_count: 0,
    });
    expect(conversation.projectId).toBeNull();
    expect(conversation.summary).toBe('尚未开始对话');
  });
});
