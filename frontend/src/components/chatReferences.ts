import type { GeneratedFile, KnowledgeAttachment, Message } from '../types';

export type ChatReferenceKind = 'attachment' | 'image' | 'generated' | 'message';

export type ChatReference = {
  kind: ChatReferenceKind;
  id: string;
  label: string;
  session_id?: string;
  preview?: string;
};

export type ChatReferenceCandidate = ChatReference & {
  group: '文档' | '图片' | '消息';
};

export type MentionQuery = {
  start: number;
  query: string;
};

const IMAGE_EXT = /\.(png|jpe?g|webp)$/i;

export function isImageAttachment(item: Pick<KnowledgeAttachment, 'name' | 'content_type' | 'extension'>): boolean {
  const extension = item.extension || '';
  return item.content_type.startsWith('image/') || IMAGE_EXT.test(item.name) || IMAGE_EXT.test(extension);
}

export function referenceToken(ref: Pick<ChatReference, 'kind' | 'label'>): string {
  const prefix = ref.kind === 'message' ? '消息' : ref.kind === 'image' ? '图片' : '文档';
  return `[@${prefix}:${ref.label}]`;
}

export function detectMention(text: string, cursor: number): MentionQuery | null {
  const before = text.slice(0, Math.max(0, cursor));
  const at = before.lastIndexOf('@');
  if (at < 0) return null;
  if (at > 0 && !/\s/.test(before[at - 1] || '')) return null;
  const query = before.slice(at + 1);
  if (query.includes('\n') || query.includes(']')) return null;
  if (/\s/.test(query)) return null;
  return { start: at, query };
}

export function filterReferenceCandidates(
  candidates: ChatReferenceCandidate[],
  query: string,
): ChatReferenceCandidate[] {
  const needle = query.trim().toLocaleLowerCase('zh-CN');
  if (!needle) return candidates.slice(0, 20);
  return candidates
    .filter((item) => `${item.label} ${item.preview || ''}`.toLocaleLowerCase('zh-CN').includes(needle))
    .slice(0, 20);
}

export function attachmentToReference(item: KnowledgeAttachment): ChatReference {
  const image = isImageAttachment(item);
  return {
    kind: image ? 'image' : 'attachment',
    id: item.id,
    label: item.name,
    preview: image ? '图片' : `${item.chunk_count} 段`,
  };
}

function isImageGenerated(file: GeneratedFile): boolean {
  return Boolean(file.mime_type?.startsWith('image/') || IMAGE_EXT.test(file.name));
}

export function buildReferenceCandidates(input: {
  attachments: KnowledgeAttachment[];
  messages: Message[];
  currentSessionId: string;
}): ChatReferenceCandidate[] {
  const files: ChatReferenceCandidate[] = [];
  const images: ChatReferenceCandidate[] = [];
  const seenAssets = new Set<string>();
  const pushAttachment = (item: KnowledgeAttachment) => {
    if (seenAssets.has(`a:${item.id}`)) return;
    seenAssets.add(`a:${item.id}`);
    const ref = attachmentToReference(item);
    const candidate = {
      ...ref,
      group: ref.kind === 'image' ? '图片' as const : '文档' as const,
    };
    if (candidate.group === '图片') images.push(candidate);
    else files.push(candidate);
  };
  for (const item of input.attachments) pushAttachment(item);
  for (const message of input.messages) {
    for (const item of message.attachments || []) pushAttachment(item);
    for (const file of message.generated_files || []) {
      const ident = file.path || file.url || file.name;
      if (!ident || seenAssets.has(`g:${ident}`)) continue;
      seenAssets.add(`g:${ident}`);
      const image = isImageGenerated(file);
      const candidate: ChatReferenceCandidate = {
        kind: image ? 'image' : 'generated',
        id: ident,
        label: file.name,
        group: image ? '图片' : '文档',
        preview: message.author_model === 'user' ? '你上传/发送' : '本对话生成',
      };
      if (image) images.push(candidate);
      else files.push(candidate);
    }
  }
  const messages = input.messages
    .filter((item) => (
      item.session_id === input.currentSessionId
      && (item.role === 'user' || item.role === 'assistant')
      && item.content.trim()
    ))
    .slice(-16)
    .reverse()
    .map((item) => ({
      kind: 'message' as const,
      id: item.id,
      label: item.content.trim().replace(/\s+/g, ' ').slice(0, 36),
      group: '消息' as const,
      preview: item.author_model === 'user' ? '你' : item.author_model,
      session_id: item.session_id,
    }));
  return [...files, ...images, ...messages];
}

export function ensureReferenceTokens(text: string, refs: ChatReference[]): string {
  let next = text.trim();
  for (const ref of refs) {
    const token = referenceToken(ref);
    if (!next.includes(token)) next = next ? `${next} ${token}` : token;
  }
  return next;
}

export function insertMentionToken(text: string, mention: MentionQuery, ref: ChatReference): string {
  return `${text.slice(0, mention.start)}${referenceToken(ref)} ${text.slice(mention.start + 1 + mention.query.length)}`;
}

function isKnowledgeAttachmentId(id: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
}

export function mergeAttachmentIds(uploadedIds: string[], refs: ChatReference[]): string[] {
  const ids: string[] = [];
  const seen = new Set<string>();
  const cited = refs
    .filter((item) => (item.kind === 'attachment' || item.kind === 'image') && isKnowledgeAttachmentId(item.id))
    .map((item) => item.id);
  for (const id of [...uploadedIds, ...cited]) {
    if (!id || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  return ids.slice(0, 10);
}
