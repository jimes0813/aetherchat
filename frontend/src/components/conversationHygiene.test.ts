import { describe, expect, it } from 'vitest';
import { isDiagnosticConversationTitle, partitionConversations } from './conversationHygiene';

describe('conversationHygiene', () => {
  it('flags smoke and schema leftover titles', () => {
    expect(isDiagnosticConversationTitle('冒烟项目DAG-planned')).toBe(true);
    expect(isDiagnosticConversationTitle('自动化schema测')).toBe(true);
    expect(isDiagnosticConversationTitle('Error: Auxiliary compression m...')).toBe(true);
    expect(isDiagnosticConversationTitle('雨夜出图')).toBe(false);
  });

  it('partitions diagnostic chats from user chats', () => {
    const { visible, diagnostic } = partitionConversations([
      { title: '你好' },
      { title: '工具调用探针' },
    ]);
    expect(visible.map((item) => item.title)).toEqual(['你好']);
    expect(diagnostic).toHaveLength(1);
  });
});
