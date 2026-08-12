import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Bot, Brain, CheckCircle2, Clock3, ExternalLink, Eye, FileText, FolderOpen, ListChecks, ShieldAlert, User, Wrench } from 'lucide-react';
import { useSessionStore } from '../stores/sessionStore';
import type { ApprovalRequest, GeneratedFile, GeneratedFileView, Message, ReasoningStep } from '../types';
import { SafeMarkdown } from './SafeMarkdown';
import { useI18n } from '../i18n';
import { ChatEmptyState } from './ChatEmptyState';

interface UnifiedFeedProps {
  approvals?: ApprovalRequest[];
  hasConfiguredModel?: boolean;
  hasLocalModel?: boolean;
  onInspectGeneratedFile?: (file: GeneratedFile) => void;
  onResolveGeneratedFile?: (file: GeneratedFile, includePreview?: boolean) => Promise<GeneratedFileView>;
  onOpenGeneratedFile?: (file: GeneratedFile, action: 'open' | 'reveal') => void;
  onOpenRunHistory?: () => void;
  onPickExample?: (prompt: string) => void;
  onOpenModels?: () => void;
}

const fileSize = (value?: number) => {
  const bytes = Math.max(0, Number(value || 0));
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const canPreviewInline = (mimeType?: string) => (
  /^(image\/(?:png|jpeg|webp|gif)|video\/|audio\/)/i.test(mimeType || '')
);

const legacyModelRequestLead = /^(?:让我|请求执行工具|我看到.+让我|我(?:先|需要|将|来)(?:检查|读取|写入|调用|创建|继续|导出|保存)|(?:好的[，,]|首先[，,]?|接下来[，,]?|现在[，,]?).*(?:让我|现在(?:我)?(?:需要|将|先|来|继续)|接下来(?:我)?(?:需要|将|先|来|继续)))/;

const withoutLegacyModelRequests = (message: Message) => {
  if (
    message.role !== 'assistant'
    || !message.reasoning_summary?.some((step) => step.phase === 'tool')
  ) {
    return message.content;
  }
  const blocks = message.content.split(/\n{2,}/);
  const requestBlocks = blocks.filter((block) => {
    const firstLine = block.trim().split('\n', 1)[0] || '';
    return legacyModelRequestLead.test(firstLine);
  });
  const stoppedBeforeFinal = message.reasoning_summary.some((step) => (
    step.phase === 'final' && /本轮提前结束|保留已经生成的部分答复/.test(step.detail)
  ));
  if (requestBlocks.length < 2 && !stoppedBeforeFinal) return message.content;
  return blocks
    .filter((block) => !requestBlocks.includes(block))
    .join('\n\n')
    .trim();
};

const GeneratedFileCard = ({
  file,
  onInspect,
  onResolve,
  onOpen,
}: {
  file: GeneratedFile;
  onInspect?: (file: GeneratedFile) => void;
  onResolve?: (file: GeneratedFile, includePreview?: boolean) => Promise<GeneratedFileView>;
  onOpen?: (file: GeneratedFile, action: 'open' | 'reveal') => void;
}) => {
  const [resolved, setResolved] = useState<GeneratedFileView>({
    location: file.path || file.url || null,
    mime_type: file.mime_type,
  });
  const [locationLoading, setLocationLoading] = useState(Boolean(file.path && onResolve));
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');

  useEffect(() => {
    let active = true;
    setResolved({
      location: file.path || file.url || null,
      mime_type: file.mime_type,
    });
    if (!file.path || !onResolve) {
      setLocationLoading(false);
      return () => { active = false; };
    }
    setLocationLoading(true);
    void onResolve(file, false)
      .then((view) => {
        if (active) setResolved((current) => ({ ...current, ...view }));
      })
      .catch(() => {
        // The relative artifact path remains useful if native resolution fails.
      })
      .finally(() => {
        if (active) setLocationLoading(false);
      });
    return () => { active = false; };
  }, [file.mime_type, file.path, file.url, onResolve]);

  useEffect(() => () => {
    if (resolved.preview_url?.startsWith('blob:')) {
      URL.revokeObjectURL(resolved.preview_url);
    }
  }, [resolved.preview_url]);

  const togglePreview = async () => {
    if (resolved.preview_url) {
      setResolved((current) => ({ ...current, preview_url: null }));
      return;
    }
    const mimeType = resolved.mime_type || file.mime_type;
    if (!canPreviewInline(mimeType) || !onResolve) {
      onInspect?.(file);
      return;
    }
    setPreviewLoading(true);
    setPreviewError('');
    try {
      const view = await onResolve(file, true);
      if (view.preview_url) {
        setResolved((current) => ({ ...current, ...view }));
      } else {
        onInspect?.(file);
      }
    } catch (error) {
      setPreviewError(String(error));
    } finally {
      setPreviewLoading(false);
    }
  };

  const mimeType = resolved.mime_type || file.mime_type || file.kind || '文件';
  return (
    <div className="rounded-lg border border-[#d8c8b7] bg-[#f7efe5] p-3">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white text-[#75502f] shadow-sm">
          <FileText size={17} />
        </div>
        <button
          type="button"
          disabled={!file.path}
          onClick={() => onInspect?.(file)}
          className="min-w-0 flex-1 text-left disabled:cursor-default"
        >
          <div className="truncate text-sm font-semibold text-[#4b3c30]">{file.name}</div>
          <div className="mt-0.5 text-[11px] text-[#776a5f]">
            {mimeType}
            {fileSize(file.size_bytes) ? ` · ${fileSize(file.size_bytes)}` : ''}
          </div>
        </button>
        <div className="flex shrink-0 flex-wrap justify-end gap-1">
          {file.path && (
            <button
              type="button"
              title={canPreviewInline(mimeType) ? '在聊天中直接预览' : '在工件面板中查看'}
              aria-label={`预览文件 ${file.name}`}
              onClick={() => void togglePreview()}
              disabled={previewLoading}
              className="flex h-8 items-center gap-1 rounded-md border border-[#cdbca9] bg-white px-2 text-xs text-[#62462e] hover:bg-[#fffaf4] disabled:opacity-50"
            >
              <Eye size={13} />
              {resolved.preview_url ? '收起' : previewLoading ? '加载中' : '预览'}
            </button>
          )}
          <button
            type="button"
            title="用默认应用打开"
            aria-label={`打开文件 ${file.name}`}
            onClick={() => onOpen?.(file, 'open')}
            className="flex h-8 items-center gap-1 rounded-md border border-[#cdbca9] bg-white px-2 text-xs text-[#62462e] hover:bg-[#fffaf4]"
          >
            <ExternalLink size={13} />
            打开
          </button>
          {file.path && (
            <button
              type="button"
              title="在文件管理器中显示"
              aria-label={`打开文件所在目录 ${file.name}`}
              onClick={() => onOpen?.(file, 'reveal')}
              className="flex h-8 items-center gap-1 rounded-md border border-[#cdbca9] bg-white px-2 text-xs text-[#62462e] hover:bg-[#fffaf4]"
            >
              <FolderOpen size={13} />
              目录
            </button>
          )}
        </div>
      </div>
      <div className="mt-2 break-all rounded-md bg-white/70 px-2 py-1.5 font-mono text-[11px] leading-4 text-[#67584c]">
        <span className="font-sans font-semibold">保存位置：</span>
        {locationLoading ? '正在读取本机路径…' : (resolved.location || '未返回保存位置')}
      </div>
      {previewError && (
        <div className="mt-2 text-xs text-[#9a3e32]">{previewError}</div>
      )}
      {resolved.preview_url && (
        <div className="mt-3 overflow-hidden rounded-lg border border-[#d8c8b7] bg-[#2b2723] p-2">
          {mimeType.startsWith('image/') && (
            <img
              src={resolved.preview_url}
              alt={file.name}
              className="mx-auto max-h-[28rem] max-w-full rounded object-contain"
            />
          )}
          {mimeType.startsWith('video/') && (
            <video src={resolved.preview_url} controls className="mx-auto max-h-[28rem] max-w-full rounded" />
          )}
          {mimeType.startsWith('audio/') && (
            <audio src={resolved.preview_url} controls className="w-full" />
          )}
        </div>
      )}
    </div>
  );
};

const ExecutionReceipt = ({
  steps,
  files,
  onOpenRunHistory,
}: {
  steps: ReasoningStep[];
  files: GeneratedFile[];
  onOpenRunHistory?: () => void;
}) => {
  const { ui } = useI18n();
  const toolSteps = steps.filter((step) => step.phase === 'tool');
  const resultSteps = steps.filter((step) => step.phase === 'result');
  const hasFailure = resultSteps.some((step) => /未成功|失败|拒绝|错误|blocked|failed|denied/i.test(step.detail));
  const requestedBrowserNavigation = toolSteps.some((step) => (
    /plugin__browser_links__open_url/i.test(step.detail)
  ));
  const hasBrowserLaunchReceipt = resultSteps.some((step) => (
    /桌面启动器已确认|launcher_accepted|execution_receipt/i.test(step.detail)
  ));
  const unverifiedLegacyNavigation = (
    requestedBrowserNavigation
    && !hasBrowserLaunchReceipt
    && !hasFailure
  );
  const needsReview = hasFailure || unverifiedLegacyNavigation;
  const finalized = steps.some((step) => step.phase === 'final');
  if (!steps.length && !files.length) return null;

  const facts = [
    { icon: Brain, label: ui('处理摘要'), value: steps.filter((step) => step.phase === 'analysis').length },
    { icon: Wrench, label: ui('工具请求'), value: toolSteps.length },
    { icon: ListChecks, label: ui('结果核对'), value: resultSteps.length },
    { icon: FileText, label: ui('交付文件'), value: files.length },
  ].filter((item) => item.value > 0);

  return (
    <section
      aria-label={ui('实际执行与交付')}
      data-execution-status={
        unverifiedLegacyNavigation
          ? 'unverified'
          : hasFailure
            ? 'failed'
            : finalized
              ? 'completed'
              : 'recorded'
      }
      className={`mt-4 rounded-lg border p-3 ${
        needsReview
          ? 'border-[#d7a98b] bg-[#fff4ec]'
          : 'border-[#b9d4c2] bg-[#f1f8f3]'
      }`}
    >
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
          needsReview ? 'bg-[#9a4c32] text-white' : 'bg-[#2f7250] text-white'
        }`}>
          {needsReview ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-[#3f463f]">
            {unverifiedLegacyNavigation
              ? ui('历史外部导航缺少桌面执行凭证')
              : hasFailure
              ? ui('执行已结束，存在需检查的结果')
              : finalized
                ? ui('本次处理已完成')
                : ui('已记录本次处理结果')}
          </div>
          <div className="mt-1 text-[11px] leading-5 text-[#647067]">
            {unverifiedLegacyNavigation
              ? ui('该历史记录只证明生成了打开链接意图，不能证明浏览器已启动；请重新发起该操作。')
              : ui('这里只汇总 AetherChat 实际观察到的编排事件、工具结果和已保存文件；模型文字本身不视为执行凭证。')}
          </div>
        </div>
        {onOpenRunHistory && (
          <button
            type="button"
            onClick={onOpenRunHistory}
            className="shrink-0 rounded-md border border-[#afc7b6] bg-white px-2.5 py-1.5 text-xs font-semibold text-[#365f46] hover:bg-[#f8fcf9]"
          >
            {ui('查看运行审计')}
          </button>
        )}
      </div>
      {facts.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {facts.map(({ icon: Icon, label, value }) => (
            <span key={label} className="inline-flex items-center gap-1.5 rounded-md border border-black/10 bg-white/80 px-2 py-1 text-[11px] text-[#4e5a51]">
              <Icon size={12} />
              {label} {value}
            </span>
          ))}
        </div>
      )}
    </section>
  );
};

export const UnifiedFeed = ({
  approvals = [],
  hasConfiguredModel = true,
  hasLocalModel = false,
  onInspectGeneratedFile,
  onResolveGeneratedFile,
  onOpenGeneratedFile,
  onOpenRunHistory,
  onPickExample,
  onOpenModels,
}: UnifiedFeedProps) => {
  const {
    messages,
  } = useSessionStore();
  const visibleMessages = useMemo(() => messages
    .map((message) => message.role === 'assistant'
      ? { ...message, content: withoutLegacyModelRequests(message) }
      : message)
    .filter((message) => (
      (message.role === 'user' || message.role === 'assistant')
      && (
        Boolean(message.content.trim())
        || Boolean(message.attachments?.length)
        || Boolean(message.generated_files?.length)
      )
    )), [messages]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { locale, t } = useI18n();

  useEffect(() => {
    const scroller = scrollRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, [approvals.length, visibleMessages.length]);

  return (
    <div ref={scrollRef} className="aether-chat-scroll flex-1 overflow-auto px-6 py-5">
      <div className="mx-auto max-w-3xl space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-normal">{t('feedTitle')}</h1>
            <p className="mt-1 text-sm text-[#6b6259]">{t('feedDescription')}</p>
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-[#e1d9cd] bg-white px-3 py-2 text-xs text-[#7a7065]">
            <Clock3 size={14} />
            {t('live')}
          </div>
        </div>

        {visibleMessages.length === 0 && (
          <ChatEmptyState
            hasConfiguredModel={hasConfiguredModel}
            hasLocalModel={hasLocalModel}
            onPickExample={(prompt) => onPickExample?.(prompt)}
            onOpenModels={onOpenModels}
            onOpenRuns={onOpenRunHistory}
          />
        )}

        {visibleMessages.map((msg) => (
          <article key={msg.id} className="rounded-lg border border-[#e1d9cd] bg-[#fffdf9] p-4 shadow-sm">
            <div className="mb-3 flex items-center gap-3">
              <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${msg.author_model === 'user' ? 'bg-[#2f2a24] text-white' : 'bg-[#ebe3d8] text-[#5b4532]'}`}>
                {msg.author_model === 'user' ? <User size={16} /> : <Bot size={16} />}
              </div>
              <div>
                <div className="text-sm font-semibold">{msg.author_model === 'user' ? t('you') : msg.author_model}</div>
                <div className="text-xs text-[#70675e]">{new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(msg.created_at))}</div>
              </div>
              {typeof msg.cost_usd === 'number' && msg.cost_usd > 0 && (
                <div className="ml-auto rounded-md bg-[#eee6db] px-2 py-1 text-xs text-[#7a552f]">
                  ${msg.cost_usd.toFixed(4)}
                </div>
              )}
            </div>
            <SafeMarkdown content={msg.content} />
            {msg.author_model !== 'user' && (
              <ExecutionReceipt
                steps={msg.reasoning_summary || []}
                files={msg.generated_files || []}
                onOpenRunHistory={onOpenRunHistory}
              />
            )}
            {msg.attachments && msg.attachments.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {msg.attachments.map((attachment) => (
                  <span key={attachment.id} className="inline-flex max-w-64 items-center gap-1.5 rounded-md border border-[#d9cbbc] bg-[#f4ece2] px-2 py-1 text-xs text-[#5b4532]" title={t('chunks', { count: attachment.chunk_count })}>
                    <FileText size={13} className="shrink-0" />
                    <span className="truncate">{attachment.name}</span>
                  </span>
                ))}
              </div>
            )}
            {msg.generated_files && msg.generated_files.length > 0 && (
              <div className="mt-4 space-y-2" aria-label="本次生成的文件">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-[#796b5e]">
                  本次生成的文件
                </div>
                {msg.generated_files.map((file, index) => (
                  <GeneratedFileCard
                    key={`${file.path || file.url || file.name}-${index}`}
                    file={file}
                    onInspect={onInspectGeneratedFile}
                    onResolve={onResolveGeneratedFile}
                    onOpen={onOpenGeneratedFile}
                  />
                ))}
              </div>
            )}
          </article>
        ))}

        {approvals.map((approval) => {
          const decisionLabel = approval.decision === 'deny'
            ? t('denied')
            : approval.decision === 'allow_session'
              ? t('sessionAllowed')
              : approval.decision === 'allow_once'
                ? t('onceAllowed')
                : '';
          if (!approval.decision) return null;
          return (
            <article
              key={approval.id}
              className={`rounded-lg border p-3 shadow-sm ${
                approval.decision === 'deny'
                  ? 'border-[#d7b1aa] bg-[#fff7f5]'
                  : 'border-[#b8ccb9] bg-[#f6fbf6]'
              }`}
              aria-label={t('approvalLabel')}
            >
              <div className="flex items-start gap-3">
                <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-white ${
                  approval.decision === 'deny' ? 'bg-[#97382c]' : 'bg-[#356b46]'
                }`}>
                  {approval.decision === 'deny' ? <ShieldAlert size={16} /> : <CheckCircle2 size={16} />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <div className="text-sm font-semibold text-[#4a4037]">{approval.title}</div>
                    <div aria-live="polite" className={`text-xs font-semibold ${approval.decision === 'deny' ? 'text-[#97382c]' : 'text-[#24613c]'}`}>
                      {decisionLabel}
                    </div>
                  </div>
                  <div className="mt-1 text-xs text-[#684a2e]">
                    {t('approvalRequest', { model: approval.model, tool: approval.tool_name })}
                  </div>
                  {approval.feedback && (
                    <div className="mt-2 rounded-md border border-[#e1c7c0] bg-white/80 px-2.5 py-2 text-xs leading-5 text-[#6f3029]">
                      {t('approvalFeedbackSummary', { feedback: approval.feedback })}
                    </div>
                  )}
                </div>
              </div>
            </article>
          );
        })}

      </div>
    </div>
  );
};
