import { describe, expect, it } from 'vitest';
import {
  buildReferenceCandidates,
  detectMention,
  ensureReferenceTokens,
  filterReferenceCandidates,
  insertMentionToken,
  mergeAttachmentIds,
  referenceToken,
} from './chatReferences';

describe('chatReferences', () => {
  it('detects an @ mention at the cursor', () => {
    expect(detectMention('请看 @封面', 6)).toEqual({ start: 3, query: '封面' });
    expect(detectMention('email@x.com', 11)).toBeNull();
    expect(detectMention('请看 @封面 然后', 10)).toBeNull();
  });

  it('builds tokens and keeps them on send', () => {
    const ref = { kind: 'attachment' as const, id: 'a1', label: 'notes.md' };
    expect(referenceToken(ref)).toBe('[@文档:notes.md]');
    expect(ensureReferenceTokens('总结一下', [ref])).toBe('总结一下 [@文档:notes.md]');
    expect(insertMentionToken('请看 @封', { start: 3, query: '封' }, {
      kind: 'image',
      id: 'img',
      label: 'cover.png',
    })).toBe('请看 [@图片:cover.png] ');
  });

  it('merges uploaded files with cited images', () => {
    expect(mergeAttachmentIds(['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'], [
      { kind: 'image', id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', label: 'b.png' },
      { kind: 'generated', id: 'generated/cover.png', label: 'cover.png' },
      { kind: 'image', id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', label: 'a.png' },
    ])).toEqual(['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb']);
  });

  it('lists only current-conversation documents, images and messages', () => {
    const candidates = buildReferenceCandidates({
      attachments: [
        { id: 'f1', name: 'notes.md', content_type: 'text/markdown', size_bytes: 12, chunk_count: 2 },
        { id: 'i1', name: 'cover.png', content_type: 'image/png', size_bytes: 20, chunk_count: 0 },
      ],
      messages: [
        {
          id: 'm1',
          session_id: 'cur',
          author_model: 'user',
          role: 'user',
          content: '先做封面',
          created_at: '2026-01-01T00:00:00Z',
        },
        {
          id: 'm2',
          session_id: 'cur',
          author_model: 'Qwen',
          role: 'assistant',
          content: '已生成封面',
          created_at: '2026-01-01T00:01:00Z',
          generated_files: [{ name: 'cover_out.png', path: 'generated/cover_out.png', mime_type: 'image/png' }],
        },
      ],
      currentSessionId: 'cur',
    });
    expect(candidates.map((item) => item.group)).toEqual(['文档', '图片', '图片', '消息', '消息']);
    expect(candidates.some((item) => item.label === 'cover_out.png')).toBe(true);
    expect(candidates.every((item) => item.group === '文档' || item.group === '图片' || item.group === '消息')).toBe(true);
    expect(filterReferenceCandidates(candidates, '先做').map((item) => item.label)).toEqual(['先做封面']);
  });
});
