/** Pure helpers for knowledge/RAG panel UX. */

export type KnowledgeLayerScores = {
  lexical?: number;
  vector?: number;
  coverage?: number;
  exact?: number;
};

export type KnowledgeHitLike = {
  citation: string;
  content: string;
  score?: number;
  match_types?: string[];
  layers?: KnowledgeLayerScores;
  rank?: number;
  source_name?: string;
  locator?: string;
};

export function formatLayerLabel(matchTypes: string[] | undefined, layers?: KnowledgeLayerScores): string {
  const types = matchTypes?.length ? matchTypes : [];
  const parts: string[] = [];
  if (types.includes('全文') || (layers?.lexical ?? 0) > 0) {
    parts.push(`全文${layers?.lexical != null ? ` ${Math.round(layers.lexical * 100)}%` : ''}`);
  }
  if (types.includes('哈希相似') || (layers?.vector ?? 0) > 0.05) {
    parts.push(`向量${layers?.vector != null ? ` ${Math.round(layers.vector * 100)}%` : ''}`);
  }
  if ((layers?.coverage ?? 0) > 0) {
    parts.push(`覆盖 ${Math.round((layers?.coverage || 0) * 100)}%`);
  }
  if ((layers?.exact ?? 0) > 0) {
    parts.push('精确命中');
  }
  if (!parts.length && types.length) {
    return types.join(' + ');
  }
  return parts.join(' · ') || '混合';
}

export function formatCitationForComposer(hit: KnowledgeHitLike): string {
  const citation = hit.citation || `${hit.source_name || '资料'} · ${hit.locator || ''}`;
  const body = (hit.content || '').trim().slice(0, 1200);
  return (
    `请基于以下项目资料回答，并在结论中标注来源。\n\n`
    + `[来源: ${citation}]\n`
    + `${body}\n`
  );
}

export function formatKnowledgeStatsLine(stats: {
  attachment_count?: number;
  ready_count?: number;
  chunk_count?: number;
  vector_count?: number;
  total_bytes?: number;
} | null | undefined): string {
  if (!stats) return '尚未加载知识库状态';
  const mb = ((stats.total_bytes || 0) / (1024 * 1024)).toFixed(2);
  return (
    `${stats.attachment_count ?? 0} 个文件`
    + ` · ${stats.ready_count ?? 0} 已就绪`
    + ` · ${stats.chunk_count ?? 0} 片段`
    + ` · ${stats.vector_count ?? 0} 向量`
    + ` · ${mb} MB`
  );
}

export function formatAskWithSources(query: string, hits: KnowledgeHitLike[], maxHits = 3): string {
  const selected = hits.slice(0, maxHits);
  if (!selected.length) {
    return query.trim();
  }
  const blocks = selected.map((hit, index) => {
    const citation = hit.citation || `来源 ${index + 1}`;
    const body = (hit.content || '').trim().slice(0, 600);
    return `[来源 ${index + 1}: ${citation}]\n${body}`;
  });
  return (
    `${query.trim()}\n\n`
    + `请仅依据下列本地检索片段作答，并用 [来源: 文件名 · 位置] 标注引用；证据不足请明确说明。\n\n`
    + blocks.join('\n\n')
  );
}
