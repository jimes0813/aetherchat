import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowUp, AtSign, AudioLines, Clapperboard, Code2, FileText, Image as ImageIcon, LoaderCircle, Paperclip, Square, X } from 'lucide-react';
import type { KnowledgeAttachment } from '../types';
import { asArray, fetchJson } from '../api/client';
import { useI18n } from '../i18n';
import { useSessionStore } from '../stores/sessionStore';
import {
  buildReferenceCandidates,
  detectMention,
  ensureReferenceTokens,
  filterReferenceCandidates,
  insertMentionToken,
  mergeAttachmentIds,
  referenceToken,
  type ChatReference,
  type ChatReferenceCandidate,
  type MentionQuery,
} from './chatReferences';
import {
  REMIX_HIGH_MODEL,
  REMIX_LOW_MODEL,
  buildComposerMessage,
  listVideoModelChoices,
  loadComfyGenerationCatalog,
  shortModelLabel,
  type ComfyGenerationCatalog,
  type ComposerIntent,
  type VideoStage,
} from './composerGeneration';

interface ComposerProps {
  providers: string[];
  onSend: (
    content: string,
    targets: string[],
    attachmentIds: string[],
    references?: ChatReference[],
  ) => boolean | void;
  onUpload: (files: File[]) => Promise<KnowledgeAttachment[]>;
  onInterrupt?: (scope: string) => void;
  draft?: { id: string; text: string; clear?: boolean } | null;
  apiBase?: string;
  catalogEnabled?: boolean;
  generationCatalog?: ComfyGenerationCatalog | null;
  onStartVoice?: () => void;
  voiceActive?: boolean;
  onConfigureModels?: () => void;
}

const INTENTS: Array<{ id: Exclude<ComposerIntent, 'chat'>; icon: typeof Code2 }> = [
  { id: 'code', icon: Code2 },
  { id: 'image', icon: ImageIcon },
  { id: 'video', icon: Clapperboard },
];

function optionClass(active: boolean) {
  return `flex h-7 w-full items-center truncate rounded-md px-2 text-left text-[11px] font-medium transition ${
    active
      ? 'bg-[#2f2a24] text-white'
      : 'text-[#5b4532] hover:bg-[#fff4e8]'
  }`;
}

export const Composer = ({
  providers,
  onSend,
  onUpload,
  onInterrupt,
  draft,
  apiBase,
  catalogEnabled = false,
  generationCatalog,
  onStartVoice,
  voiceActive = false,
  onConfigureModels,
}: ComposerProps) => {
  const initialSessionId = useSessionStore.getState().sessionId;
  const [input, setInput] = useState(
    () => useSessionStore.getState().composerDrafts[initialSessionId] || '',
  );
  const [target, setTarget] = useState('all');
  const [attachments, setAttachments] = useState<KnowledgeAttachment[]>([]);
  const [libraryAttachments, setLibraryAttachments] = useState<KnowledgeAttachment[]>([]);
  const [references, setReferences] = useState<ChatReference[]>([]);
  const [mention, setMention] = useState<MentionQuery | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [intent, setIntent] = useState<ComposerIntent>('chat');
  const [catalog, setCatalog] = useState<ComfyGenerationCatalog | null>(generationCatalog ?? null);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [imageModel, setImageModel] = useState('');
  const [videoChoiceName, setVideoChoiceName] = useState('');
  const [videoStage, setVideoStage] = useState<VideoStage>('high');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerKind, setPickerKind] = useState<'image' | 'video'>('image');
  const [pickerStyle, setPickerStyle] = useState<{ left: number; bottom: number; width: number }>({
    left: 8,
    bottom: 8,
    width: 288,
  });
  const fileInput = useRef<HTMLInputElement>(null);
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const pickerRef = useRef<HTMLDivElement>(null);
  const pickerCardRef = useRef<HTMLDivElement>(null);
  const mentionMenuRef = useRef<HTMLDivElement>(null);
  const hoverCloseTimer = useRef<number | null>(null);
  const { t, ui } = useI18n();
  const sessionId = useSessionStore((state) => state.sessionId);
  const messages = useSessionStore((state) => state.messages);
  const setComposerDraft = useSessionStore((state) => state.setComposerDraft);

  useEffect(() => {
    setComposerDraft(sessionId, input);
  }, [input, sessionId, setComposerDraft]);

  useEffect(() => {
    if (target !== 'all' && !providers.includes(target)) setTarget('all');
  }, [providers, target]);

  useEffect(() => {
    if (!draft) return;
    if (draft.clear || draft.text) setInput(draft.text);
    window.requestAnimationFrame(() => {
      composerInput.current?.focus();
      const length = composerInput.current?.value.length ?? 0;
      composerInput.current?.setSelectionRange(length, length);
    });
  }, [draft?.id]);

  useEffect(() => {
    if (!apiBase || !catalogEnabled || !sessionId) return;
    let cancelled = false;
    fetchJson<unknown>(`${apiBase}/knowledge/${sessionId}/attachments`)
      .then((items) => {
        if (!cancelled) setLibraryAttachments(asArray<KnowledgeAttachment>(items));
      })
      .catch(() => {
        if (!cancelled) setLibraryAttachments([]);
      });
    return () => {
      cancelled = true;
    };
  }, [apiBase, catalogEnabled, sessionId]);

  const referenceLibrary = useMemo(() => {
    const seen = new Set<string>();
    const items: KnowledgeAttachment[] = [];
    for (const item of [...attachments, ...libraryAttachments]) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      items.push(item);
    }
    return items;
  }, [attachments, libraryAttachments]);

  const mentionCandidates = useMemo(
    () => filterReferenceCandidates(
      buildReferenceCandidates({
        attachments: referenceLibrary,
        messages,
        currentSessionId: sessionId,
      }),
      mention?.query || '',
    ),
    [mention?.query, messages, referenceLibrary, sessionId],
  );

  useEffect(() => {
    const el = composerInput.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 44), 200)}px`;
  }, [input]);

  const refreshCatalog = async (startIfNeeded: boolean) => {
    if (generationCatalog !== undefined) {
      setCatalog(generationCatalog);
      setCatalogError('');
      return;
    }
    if (!apiBase || !catalogEnabled) return;
    setCatalogBusy(true);
    setCatalogError('');
    try {
      const next = await loadComfyGenerationCatalog(apiBase, { startIfNeeded });
      setCatalog(next);
      if (!next.image_models.length && !next.video_pipelines.length) {
        setCatalogError(t('composerNoModels'));
      }
    } catch (error) {
      setCatalog(null);
      setCatalogError(error instanceof Error ? error.message : String(error));
    } finally {
      setCatalogBusy(false);
    }
  };

  useEffect(() => {
    if (generationCatalog !== undefined) {
      setCatalog(generationCatalog);
      return;
    }
    if (!catalogEnabled || !apiBase) return;
    void refreshCatalog(false);
  }, [apiBase, catalogEnabled, generationCatalog]);

  const imageModels = catalog?.image_models ?? [];
  const videoChoices = useMemo(
    () => listVideoModelChoices(catalog?.video_pipelines ?? []),
    [catalog],
  );
  const remixHigh = videoChoices.find((item) => /remix/i.test(item.name) && !/low/i.test(item.name))?.name || REMIX_HIGH_MODEL;
  const remixLow = videoChoices.find((item) => /remix/i.test(item.name) && /low/i.test(item.name))?.name
    || videoChoices.find((item) => item.companionModels.some((name) => /low/i.test(name)))?.companionModels.find((name) => /low/i.test(name))
    || REMIX_LOW_MODEL;
  const selectedVideoChoice = videoChoices.find((item) => item.name === videoChoiceName)
    || videoChoices.find((item) => item.name === (videoStage === 'low' ? remixLow : remixHigh))
    || videoChoices[0];
  const resolvedImageModel = imageModel || imageModels.find((item) => item.recommended)?.name || imageModels[0]?.name || '';
  const resolvedVideoModel = (() => {
    const selected = selectedVideoChoice;
    if (selected && !/remix/i.test(selected.name) && selected.companionModels.length === 0) {
      return selected.name;
    }
    if (videoStage === 'low') return remixLow;
    return selected && !/low/i.test(selected.name) ? selected.name : remixHigh;
  })();

  useEffect(() => {
    if (!imageModel && imageModels[0]) {
      setImageModel(imageModels.find((item) => item.recommended)?.name || imageModels[0].name);
    }
  }, [imageModel, imageModels]);

  useEffect(() => {
    if (!videoChoiceName && (videoChoices[0] || remixHigh)) {
      const recommended = videoChoices.find((item) => /remix/i.test(item.name) && !/low/i.test(item.name))
        || videoChoices[0];
      setVideoChoiceName(recommended?.name || remixHigh);
    }
  }, [videoChoiceName, videoChoices, remixHigh]);

  const placeholder = (
    intent === 'code' ? t('composerCodePlaceholder')
      : intent === 'image' ? t('composerImagePlaceholder')
        : intent === 'video' ? t('composerVideoPlaceholder')
          : t('composerPlaceholder')
  );

  const send = () => {
    if (providers.length === 0) return;
    const hasImageAttachment = attachments.some((item) => Boolean(item.preview_url))
      || references.some((item) => item.kind === 'image');
    const raw = buildComposerMessage({
      intent,
      text: input,
      imageModel: resolvedImageModel,
      videoModel: resolvedVideoModel,
      hasImageAttachment,
    });
    const content = ensureReferenceTokens(raw, references);
    const attachmentIds = mergeAttachmentIds(attachments.map((item) => item.id), references);
    if ((!content && attachmentIds.length === 0 && references.length === 0) || uploading) return;
    const sent = references.length
      ? onSend(content, [target], attachmentIds, references)
      : onSend(content, [target], attachmentIds);
    if (sent !== false) {
      setInput('');
      setMention(null);
      attachments.forEach((item) => item.preview_url && URL.revokeObjectURL(item.preview_url));
      setAttachments([]);
      setReferences([]);
    }
  };

  const uploadFiles = async (files: File[]) => {
    const supported = files.filter((file) => /\.(png|jpe?g|webp|txt|md|csv|json|pdf|docx|xlsx|pptx|py|js|ts|tsx|jsx|css|html|ya?ml|toml|xml|sql|sh|go|rs|java|c|cpp|h)$/i.test(file.name));
    if (!supported.length) {
      setUploadError('支持 PNG/JPEG/WebP、常见文本/代码、PDF 和 Office 文件');
      return;
    }
    setUploading(true);
    setUploadError('');
    try {
      const uploaded = await onUpload(supported.slice(0, Math.max(0, 10 - attachments.length)));
      setAttachments((current) => [...current, ...uploaded].slice(0, 10));
      setLibraryAttachments((current) => [...uploaded, ...current]);
      setReferences((current) => {
        const next = [...current];
        for (const item of uploaded) {
          const kind = item.content_type.startsWith('image/') ? 'image' as const : 'attachment' as const;
          if (next.some((ref) => ref.id === item.id)) continue;
          next.push({ kind, id: item.id, label: item.name, preview: kind === 'image' ? '图片' : `${item.chunk_count} 段` });
        }
        return next.slice(0, 8);
      });
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : String(error));
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = '';
    }
  };

  const handleInterrupt = () => {
    if (onInterrupt) onInterrupt(target === 'all' ? 'all' : target);
  };

  const clearHoverClose = () => {
    if (hoverCloseTimer.current != null) {
      window.clearTimeout(hoverCloseTimer.current);
      hoverCloseTimer.current = null;
    }
  };

  const openPicker = (kind: 'image' | 'video') => {
    clearHoverClose();
    setPickerKind(kind);
    setPickerOpen(true);
  };

  const scheduleClosePicker = () => {
    clearHoverClose();
    hoverCloseTimer.current = window.setTimeout(() => setPickerOpen(false), 160);
  };

  const toggleIntent = (next: Exclude<ComposerIntent, 'chat'>) => {
    setIntent((current) => {
      const resolved = current === next ? 'chat' : next;
      if (resolved === 'image' || resolved === 'video') openPicker(resolved);
      else setPickerOpen(false);
      return resolved;
    });
    if (next !== 'code') {
      void refreshCatalog(!catalog?.image_models.length && !catalog?.video_pipelines.length);
    }
    window.requestAnimationFrame(() => composerInput.current?.focus());
  };

  const updatePickerPosition = () => {
    const anchor = pickerRef.current;
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(288, Math.max(220, window.innerWidth - 16));
    let left = rect.left;
    if (left + width > window.innerWidth - 8) left = window.innerWidth - 8 - width;
    if (left < 8) left = 8;
    setPickerStyle({
      left,
      bottom: Math.max(8, window.innerHeight - rect.top + 8),
      width,
    });
  };

  useLayoutEffect(() => {
    if (!mention) return undefined;
    const update = () => {
      const el = composerInput.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const width = Math.min(320, Math.max(220, rect.width));
      let left = rect.left;
      if (left + width > window.innerWidth - 8) left = window.innerWidth - 8 - width;
      if (left < 8) left = 8;
      setPickerStyle({
        left,
        bottom: Math.max(8, window.innerHeight - rect.top + 8),
        width,
      });
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [mention, mentionCandidates.length]);

  useLayoutEffect(() => {
    if (!pickerOpen) return undefined;
    updatePickerPosition();
    const onReposition = () => updatePickerPosition();
    window.addEventListener('resize', onReposition);
    window.addEventListener('scroll', onReposition, true);
    return () => {
      window.removeEventListener('resize', onReposition);
      window.removeEventListener('scroll', onReposition, true);
    };
  }, [pickerOpen, pickerKind]);

  useEffect(() => {
    if (!pickerOpen && !mention) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (pickerRef.current?.contains(target) || pickerCardRef.current?.contains(target)) return;
      if (mentionMenuRef.current?.contains(target) || composerInput.current?.contains(target)) return;
      setPickerOpen(false);
      setMention(null);
    };
    window.addEventListener('pointerdown', onPointerDown);
    return () => window.removeEventListener('pointerdown', onPointerDown);
  }, [mention, pickerOpen]);

  useEffect(() => () => clearHoverClose(), []);

  const selectVideoStage = (stage: VideoStage) => {
    setVideoStage(stage);
    setVideoChoiceName(stage === 'low' ? remixLow : remixHigh);
  };

  const applyMention = (candidate: ChatReferenceCandidate) => {
    const ref: ChatReference = {
      kind: candidate.kind,
      id: candidate.id,
      label: candidate.label,
      preview: candidate.preview,
      session_id: candidate.session_id,
    };
    setReferences((current) => (
      current.some((item) => item.kind === ref.kind && item.id === ref.id)
        ? current
        : [...current, ref].slice(0, 8)
    ));
    const activeMention = mention || detectMention(input, input.length);
    setInput((current) => (
      activeMention ? insertMentionToken(current, activeMention, ref) : `${current.trim()} ${referenceToken(ref)} `.trimStart()
    ));
    setMention(null);
    window.requestAnimationFrame(() => composerInput.current?.focus());
  };

  const removeReference = (ref: ChatReference) => {
    setReferences((current) => current.filter((item) => !(item.kind === ref.kind && item.id === ref.id)));
    const token = referenceToken(ref);
    setInput((current) => current.replace(token, '').replace(/\s{2,}/g, ' ').trimStart());
    if (ref.kind === 'attachment' || ref.kind === 'image') {
      setAttachments((current) => current.filter((item) => item.id !== ref.id));
    }
  };

  return (
    <div
      className="bg-transparent px-4 pb-4 pt-1"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        void uploadFiles(Array.from(event.dataTransfer.files));
      }}
    >
      <div className="mx-auto max-w-2xl">
        {(references.length > 0 || attachments.length > 0 || uploading || uploadError) && (
          <div className="mb-2 flex flex-wrap items-center gap-2" aria-live="polite">
            {references.map((ref) => (
              <span key={`${ref.kind}-${ref.id}`} className="inline-flex max-w-56 items-center gap-1 rounded-full border border-[#d9cbbc] bg-[#f4ece2] px-2 py-1 text-[11px] text-[#5b4532]">
                <span className="truncate">{referenceToken(ref)}</span>
                <button type="button" aria-label={t('remove', { name: ref.label })} onClick={() => removeReference(ref)}>
                  <X size={12} />
                </button>
              </span>
            ))}
            {attachments.filter((item) => !references.some((ref) => ref.id === item.id)).map((attachment) => (
              <span key={attachment.id} className="inline-flex max-w-64 items-center gap-1.5 rounded-md border border-[#d9cbbc] bg-[#f4ece2] px-2 py-1 text-xs text-[#5b4532]">
                {attachment.preview_url ? <img src={attachment.preview_url} alt="" className="h-7 w-7 shrink-0 rounded object-cover" /> : <FileText size={13} className="shrink-0" />}
                <span className="truncate">{attachment.name}</span>
                <button type="button" aria-label={t('remove', { name: attachment.name })} onClick={() => {
                  if (attachment.preview_url) URL.revokeObjectURL(attachment.preview_url);
                  setAttachments((items) => items.filter((item) => item.id !== attachment.id));
                }}>
                  <X size={13} />
                </button>
              </span>
            ))}
            {uploading && <span className="inline-flex items-center gap-1 text-xs text-[#6b6259]"><LoaderCircle size={13} className="animate-spin" />{t('parsing')}</span>}
            {uploadError && <span role="alert" className="text-xs text-red-700">{uploadError}</span>}
          </div>
        )}

        <div data-aether-composer-card className="rounded-[1.35rem] border border-[#e4dbd0] bg-white shadow-[0_8px_28px_rgba(47,42,36,0.08)]">
          <input
            ref={fileInput}
            type="file"
            multiple
            accept=".png,.jpg,.jpeg,.webp,.txt,.md,.csv,.json,.pdf,.docx,.xlsx,.pptx,.py,.js,.ts,.tsx,.jsx,.css,.html,.yaml,.yml,.toml,.xml,.sql,.sh,.go,.rs,.java,.c,.cpp,.h"
            className="hidden"
            onChange={(event) => void uploadFiles(Array.from(event.target.files ?? []))}
          />
          <textarea
            ref={composerInput}
            data-aether-composer
            value={input}
            rows={1}
            onChange={(event) => {
              const value = event.target.value;
              setInput(value);
              const next = detectMention(value, event.target.selectionStart ?? value.length);
              setMention(next);
              setMentionIndex(0);
            }}
            onKeyDown={(event) => {
              if (mention && mentionCandidates.length) {
                if (event.key === 'ArrowDown') {
                  event.preventDefault();
                  setMentionIndex((index) => Math.min(index + 1, mentionCandidates.length - 1));
                  return;
                }
                if (event.key === 'ArrowUp') {
                  event.preventDefault();
                  setMentionIndex((index) => Math.max(index - 1, 0));
                  return;
                }
                if (event.key === 'Enter' || event.key === 'Tab') {
                  event.preventDefault();
                  applyMention(mentionCandidates[mentionIndex] || mentionCandidates[0]);
                  return;
                }
                if (event.key === 'Escape') {
                  event.preventDefault();
                  setMention(null);
                  return;
                }
              }
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
            aria-label={placeholder}
            placeholder={placeholder}
            className="max-h-[12.5rem] min-h-[2.75rem] w-full resize-none bg-transparent px-4 pt-3.5 text-[15px] leading-6 outline-none"
          />

          <div className="aether-composer-toolbar flex h-11 flex-nowrap items-center gap-1 overflow-hidden px-2 pb-1.5">
            <button
              type="button"
              title={t('addAttachment')}
              aria-label={t('addAttachment')}
              disabled={uploading || attachments.length >= 10}
              onClick={() => fileInput.current?.click()}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[#6b6259] transition hover:bg-[#f3ece3] disabled:opacity-50"
            >
              <Paperclip size={16} />
            </button>
            <button
              type="button"
              title={ui('引用当前对话中的消息、图片或文档')}
              aria-label={ui('引用当前对话中的消息、图片或文档')}
              onClick={() => {
                const cursor = composerInput.current?.selectionStart ?? input.length;
                const existing = detectMention(input, cursor);
                if (existing) {
                  setMention(existing);
                  return;
                }
                const next = `${input}${input && !input.endsWith(' ') && !input.endsWith('\n') ? ' ' : ''}@`;
                setInput(next);
                setMention({ start: next.length - 1, query: '' });
                setMentionIndex(0);
                window.requestAnimationFrame(() => composerInput.current?.focus());
              }}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[#6b6259] transition hover:bg-[#f3ece3]"
            >
              <AtSign size={16} />
            </button>
            <div
              ref={pickerRef}
              className="flex min-w-0 items-center gap-0.5"
              onMouseLeave={scheduleClosePicker}
            >
              {INTENTS.map((item) => {
                const active = intent === item.id;
                const Icon = item.icon;
                const label = item.id === 'code' ? t('composerCode') : item.id === 'image' ? t('composerImage') : t('composerVideo');
                return (
                  <button
                    key={item.id}
                    type="button"
                    aria-pressed={active}
                    aria-haspopup={item.id === 'image' || item.id === 'video' ? 'listbox' : undefined}
                    aria-expanded={item.id !== 'code' && pickerOpen && pickerKind === item.id}
                    onMouseEnter={() => {
                      if (item.id === 'image' || item.id === 'video') {
                        openPicker(item.id);
                        void refreshCatalog(false);
                      } else {
                        setPickerOpen(false);
                      }
                    }}
                    onClick={() => toggleIntent(item.id)}
                    className={`inline-flex h-8 shrink-0 items-center gap-1 rounded-full px-2 text-[12px] transition ${
                      active
                        ? 'bg-[#2f2a24] text-white'
                        : 'text-[#5b4532] hover:bg-[#f3ece3]'
                    }`}
                  >
                    <Icon size={13} />
                    {label}
                  </button>
                );
              })}
            </div>
            <div className="ml-auto flex shrink-0 items-center gap-1.5">
              <select
                aria-label={t('allModels')}
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                className="max-w-[8.5rem] truncate rounded-full bg-transparent px-1.5 py-1 text-[12px] text-[#6b6259] outline-none"
              >
                <option value="all">{t('allModels')}</option>
                {providers.map((provider) => (
                  <option key={provider} value={provider}>@{provider}</option>
                ))}
              </select>
              {onInterrupt && (
                <button
                  type="button"
                  onClick={handleInterrupt}
                  aria-label={t('interrupt', { target: target === 'all' ? t('allModels').replace('@', '') : `@${target}` })}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-red-600 text-white transition hover:bg-red-700"
                >
                  <Square size={11} fill="currentColor" />
                </button>
              )}
              {onStartVoice && (
                <button
                  type="button"
                  onClick={onStartVoice}
                  aria-label={voiceActive ? ui('语音对话进行中') : ui('开始语音对话')}
                  aria-pressed={voiceActive}
                  className={`flex h-8 w-8 items-center justify-center rounded-full transition ${
                    voiceActive
                      ? 'bg-[#8d5f35] text-white'
                      : 'text-[#6b6259] hover:bg-[#f3ece3]'
                  }`}
                >
                  <AudioLines size={16} />
                </button>
              )}
              {providers.length === 0 ? (
                <button
                  type="button"
                  onClick={onConfigureModels}
                  className="h-8 rounded-full bg-[#2f2a24] px-3 text-xs font-medium text-white transition hover:bg-[#433a32]"
                >
                  {t('configureModels')}
                </button>
              ) : (
                <button
                  type="button"
                  disabled={uploading || (!input.trim() && attachments.length === 0 && references.length === 0 && intent === 'chat')}
                  onClick={send}
                  aria-label={t('send')}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-[#2f2a24] text-white transition hover:bg-[#433a32] disabled:cursor-not-allowed disabled:opacity-35"
                >
                  <ArrowUp size={16} />
                </button>
              )}
            </div>
          </div>
        </div>
        {pickerOpen && createPortal(
          <div
            ref={pickerCardRef}
            role="listbox"
            data-testid="generation-model-card"
            aria-label={pickerKind === 'image' ? t('composerImageModel') : t('composerVideoModel')}
            onMouseEnter={clearHoverClose}
            onMouseLeave={scheduleClosePicker}
            style={{
              position: 'fixed',
              left: pickerStyle.left,
              bottom: pickerStyle.bottom,
              width: pickerStyle.width,
              zIndex: 80,
            }}
            className="aether-model-picker-card rounded-lg border border-[#d8cfc3] bg-[#fffdf9] p-2 shadow-[0_8px_24px_rgba(47,42,36,0.16)]"
          >
            <div className="mb-1.5 flex items-center justify-between gap-2 text-[11px] font-semibold text-[#62584e]">
              <span>{pickerKind === 'image' ? t('composerImageModel') : t('composerVideoModel')}</span>
              <button type="button" className="font-medium text-[#8d5f35] hover:underline" onClick={() => void refreshCatalog(true)} disabled={catalogBusy}>
                {catalogBusy ? t('composerLoadingModels') : t('composerStartModels')}
              </button>
            </div>
            {pickerKind === 'video' && (
              <div className="mb-2 inline-flex rounded-full border border-[#e4d8c8] bg-[#fffaf4] p-0.5" role="group" aria-label={t('composerVideoStage')}>
                {(['high', 'low'] as const).map((stage) => (
                  <button
                    key={stage}
                    type="button"
                    aria-pressed={videoStage === stage}
                    onClick={() => {
                      setIntent('video');
                      selectVideoStage(stage);
                    }}
                    className={`h-6 min-w-[2.5rem] rounded-full px-2 text-[11px] font-semibold ${
                      videoStage === stage ? 'bg-[#2f2a24] text-white' : 'text-[#5b4532]'
                    }`}
                  >
                    {stage === 'high' ? t('composerVideoHigh') : t('composerVideoLow')}
                  </button>
                ))}
              </div>
            )}
            <div className="flex max-h-56 flex-col gap-1 overflow-y-auto">
              {pickerKind === 'image' && imageModels.map((model) => (
                <button
                  key={model.name}
                  type="button"
                  role="option"
                  title={model.name}
                  aria-selected={resolvedImageModel === model.name}
                  onClick={() => {
                    setIntent('image');
                    setImageModel(model.name);
                  }}
                  className={optionClass(resolvedImageModel === model.name)}
                >
                  {shortModelLabel(model.name)}{model.recommended ? ` · ${t('composerRecommended')}` : ''}
                </button>
              ))}
              {pickerKind === 'image' && !imageModels.length && !catalogBusy && (
                <span className="text-[11px] text-[#8a3b31]">{catalogError || t('composerNoModels')}</span>
              )}
              {pickerKind === 'video' && videoChoices.map((choice) => (
                <button
                  key={choice.name}
                  type="button"
                  role="option"
                  title={choice.name}
                  aria-selected={resolvedVideoModel === choice.name || videoChoiceName === choice.name}
                  onClick={() => {
                    setIntent('video');
                    setVideoChoiceName(choice.name);
                    if (/low/i.test(choice.name)) setVideoStage('low');
                    else if (/high|remix/i.test(choice.name)) setVideoStage('high');
                  }}
                  className={optionClass(resolvedVideoModel === choice.name || videoChoiceName === choice.name)}
                >
                  {choice.label}
                </button>
              ))}
              {pickerKind === 'video' && !videoChoices.length && (
                <>
                  <button type="button" role="option" aria-selected={videoStage === 'high'} onClick={() => { setIntent('video'); selectVideoStage('high'); }} className={optionClass(videoStage === 'high')}>
                    Remix High
                  </button>
                  <button type="button" role="option" aria-selected={videoStage === 'low'} onClick={() => { setIntent('video'); selectVideoStage('low'); }} className={optionClass(videoStage === 'low')}>
                    Remix Low
                  </button>
                </>
              )}
            </div>
          </div>,
          document.body,
        )}
        {mention && createPortal(
          <div
            ref={mentionMenuRef}
            role="listbox"
            aria-label={ui('引用当前对话中的消息、图片或文档')}
            style={{
              position: 'fixed',
              left: pickerStyle.left,
              bottom: pickerStyle.bottom,
              width: pickerStyle.width,
              zIndex: 85,
            }}
            className="max-h-72 overflow-auto rounded-xl border border-[#d8cfc3] bg-[#fffdf9] p-1.5 shadow-[0_8px_24px_rgba(47,42,36,0.16)]"
          >
            {mentionCandidates.length === 0 && (
              <div className="px-2 py-3 text-[12px] text-[#8b8176]">
                {ui('当前对话还没有可引用的消息、图片或文档')}
              </div>
            )}
            {mentionCandidates.map((item, index) => (
              <button
                key={`${item.kind}-${item.id}`}
                type="button"
                role="option"
                aria-selected={index === mentionIndex}
                onMouseEnter={() => setMentionIndex(index)}
                onClick={() => applyMention(item)}
                className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left ${
                  index === mentionIndex ? 'bg-[#2f2a24] text-white' : 'text-[#4a4037] hover:bg-[#fff4e8]'
                }`}
              >
                <span className="min-w-0 truncate text-[13px]">{item.label}</span>
                <span className={`shrink-0 text-[11px] ${index === mentionIndex ? 'text-white/70' : 'text-[#8b8176]'}`}>
                  {item.group}
                </span>
              </button>
            ))}
          </div>,
          document.body,
        )}
      </div>
    </div>
  );
};
