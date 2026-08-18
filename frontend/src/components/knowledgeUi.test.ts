import { describe, expect, it } from 'vitest';
import {
  formatAskWithSources,
  formatCitationForComposer,
  formatKnowledgeStatsLine,
  formatLayerLabel,
} from './knowledgeUi';

describe('knowledgeUi helpers', () => {
  it('formats hybrid layer labels', () => {
    expect(formatLayerLabel(['全文', '哈希相似'], {
      lexical: 0.8,
      vector: 0.42,
      coverage: 0.5,
      exact: 1,
    })).toContain('全文');
    expect(formatLayerLabel(['全文', '哈希相似'], {
      lexical: 0.8,
      vector: 0.42,
      coverage: 0.5,
      exact: 1,
    })).toContain('向量');
    expect(formatLayerLabel(['全文', '哈希相似'], {
      lexical: 0.8,
      vector: 0.42,
      coverage: 0.5,
      exact: 1,
    })).toContain('精确命中');
    expect(formatLayerLabel(['全文', '语义'])).toBe('全文 · 向量');
  });

  it('builds composer citation blocks', () => {
    const text = formatCitationForComposer({
      citation: 'notes.md · 全文',
      content: '本地 FTS 检索说明',
    });
    expect(text).toContain('[来源: notes.md · 全文]');
    expect(text).toContain('本地 FTS 检索说明');
  });

  it('formats stats strip', () => {
    expect(formatKnowledgeStatsLine({
      attachment_count: 2,
      ready_count: 2,
      chunk_count: 12,
      vector_count: 12,
      total_bytes: 1024 * 1024,
    })).toContain('2 个文件');
    expect(formatKnowledgeStatsLine(null)).toContain('尚未加载');
  });

  it('asks with top sources', () => {
    const prompt = formatAskWithSources('什么是混合检索？', [
      { citation: 'a.md · 1', content: 'BM25' },
      { citation: 'b.md · 2', content: '向量' },
    ]);
    expect(prompt.startsWith('什么是混合检索？')).toBe(true);
    expect(prompt).toContain('来源 1');
    expect(prompt).toContain('BM25');
  });
});
