import { useEffect, useRef, useState } from 'react';
import { FileText, LoaderCircle, Paperclip, Send, UsersRound, X } from 'lucide-react';
import type { KnowledgeAttachment } from '../types';
import { useI18n } from '../i18n';

interface ComposerProps {
  providers: string[];
  onSend: (content: string, targets: string[], attachmentIds: string[]) => boolean | void;
  onUpload: (files: File[]) => Promise<KnowledgeAttachment[]>;
  onInterrupt?: (scope: string) => void;
}

export const Composer = ({ providers, onSend, onUpload, onInterrupt }: ComposerProps) => {
  const [input, setInput] = useState('');
  const [target, setTarget] = useState('all');
  const [attachments, setAttachments] = useState<KnowledgeAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const { t } = useI18n();

  useEffect(() => {
    if (target !== 'all' && !providers.includes(target)) setTarget('all');
  }, [providers, target]);

  const send = () => {
    const content = input.trim();
    if ((!content && attachments.length === 0) || uploading) return;
    const sent = onSend(content, [target], attachments.map((item) => item.id));
    if (sent !== false) {
      setInput('');
      attachments.forEach((item) => item.preview_url && URL.revokeObjectURL(item.preview_url));
      setAttachments([]);
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

  return (
    <div
      className="border-t border-[#e7dfd3] bg-[#fbfaf7] p-4"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        void uploadFiles(Array.from(event.dataTransfer.files));
      }}
    >
      <div className="mx-auto max-w-3xl">
        {(attachments.length > 0 || uploading || uploadError) && (
          <div className="mb-2 flex flex-wrap items-center gap-2" aria-live="polite">
            {attachments.map((attachment) => (
              <span key={attachment.id} className="inline-flex max-w-64 items-center gap-1.5 rounded-md border border-[#d9cbbc] bg-[#f4ece2] px-2 py-1 text-xs text-[#5b4532]">
                {attachment.preview_url ? <img src={attachment.preview_url} alt="" className="h-7 w-7 shrink-0 rounded object-cover" /> : <FileText size={13} className="shrink-0" />}
                <span className="truncate">{attachment.name}</span>
                <span className="shrink-0 text-[#70675e]">{t('chunks', { count: attachment.chunk_count })}</span>
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
        <div className="flex gap-3">
        <div className="flex items-center gap-2 rounded-lg border border-[#ded6ca] bg-white px-3">
          <UsersRound size={16} className="text-[#7a7065]" />
          <select aria-label={t('allModels')} value={target} onChange={(event) => setTarget(event.target.value)} className="h-11 bg-transparent text-sm font-medium text-[#4a4037] outline-none">
            <option value="all">{t('allModels')}</option>
            {providers.map((provider) => (
              <option key={provider} value={provider}>@{provider}</option>
            ))}
          </select>
        </div>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept=".png,.jpg,.jpeg,.webp,.txt,.md,.csv,.json,.pdf,.docx,.xlsx,.pptx,.py,.js,.ts,.tsx,.jsx,.css,.html,.yaml,.yml,.toml,.xml,.sql,.sh,.go,.rs,.java,.c,.cpp,.h"
          className="hidden"
          onChange={(event) => void uploadFiles(Array.from(event.target.files ?? []))}
        />
        <button
          type="button"
          title={t('addAttachment')}
          aria-label={t('addAttachment')}
          disabled={uploading || attachments.length >= 10}
          onClick={() => fileInput.current?.click()}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-[#ded6ca] bg-white text-[#5b4532] transition hover:bg-[#f3ece3] disabled:opacity-50"
        >
          <Paperclip size={17} />
        </button>
        <textarea
          data-aether-composer
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              send();
            }
          }}
          aria-label={t('composerPlaceholder')}
          placeholder={t('composerPlaceholder')}
          className="min-h-11 flex-1 resize-none rounded-lg border border-[#ded6ca] bg-white px-4 py-3 text-sm leading-5 outline-none transition focus:border-[#8d5f35] focus:ring-4 focus:ring-[#eadfd1]"
        />
        <button disabled={uploading || (!input.trim() && attachments.length === 0)} onClick={send} className="flex h-11 items-center gap-2 rounded-lg bg-[#2f2a24] px-4 text-sm font-semibold text-white transition hover:bg-[#433a32] disabled:cursor-not-allowed disabled:opacity-50">
          <Send size={16} />
          {t('send')}
        </button>
        {onInterrupt && (
          <button onClick={handleInterrupt} className="flex h-11 items-center gap-2 rounded-lg bg-red-600 px-3 text-sm font-semibold text-white transition hover:bg-red-700">
            {t('interrupt', { target: target === 'all' ? t('allModels').replace('@', '') : `@${target}` })}
          </button>
        )}
        </div>
      </div>
    </div>
  );
};
