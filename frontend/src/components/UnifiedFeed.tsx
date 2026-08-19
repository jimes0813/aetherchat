import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, ExternalLink, Eye, FileText, FolderOpen } from 'lucide-react';
import { useSessionStore } from '../stores/sessionStore';
import type { ApprovalRequest, GeneratedFile, GeneratedFileView, Message, ReasoningStep } from '../types';
import { SafeMarkdown } from './SafeMarkdown';
import { useI18n } from '../i18n';
import { ApprovalPrompt } from './ApprovalPrompt';
import { ChatEmptyState } from './ChatEmptyState';
import { parseToolActivity } from './toolDisplay';
import {
  displayGeneratedFileName,
  technicalGeneratedFiles,
  visibleGeneratedFiles,
} from './generatedFiles';

interface UnifiedFeedProps {
  approvals?: ApprovalRequest[];
  hasConfiguredModel?: boolean;
  hasLocalModel?: boolean;
  onInspectGeneratedFile?: (file: GeneratedFile) => void;
  onResolveGeneratedFile?: (file: GeneratedFile, includePreview?: boolean) => Promise<GeneratedFileView>;
  onOpenGeneratedFile?: (file: GeneratedFile, action: 'open' | 'reveal') => void;
  onOpenRunHistory?: () => void;
  onPickExample?: (prompt: string) => void;
  onApprovalDecision?: (
    approval: ApprovalRequest,
    decision: NonNullable<ApprovalRequest['decision']>,
    feedback?: string,
  ) => void | Promise<void>;
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
  const { ui } = useI18n();
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
          <div className="truncate text-sm font-semibold text-[#4b3c30]">{displayGeneratedFileName(file)}</div>
          <div className="mt-0.5 text-[11px] text-[#776a5f]">
            {mimeType}
            {fileSize(file.size_bytes) ? ` · ${fileSize(file.size_bytes)}` : ''}
          </div>
        </button>
        <div className="flex shrink-0 flex-wrap justify-end gap-1">
          {file.path && (
            <button
              type="button"
              title={canPreviewInline(mimeType) ? ui('在聊天中直接预览') : ui('在工件面板中查看')}
              aria-label={ui('预览文件 {name}', { name: file.name })}
              onClick={() => void togglePreview()}
              disabled={previewLoading}
              className="flex h-8 items-center gap-1 rounded-md border border-[#cdbca9] bg-white px-2 text-xs text-[#62462e] hover:bg-[#fffaf4] disabled:opacity-50"
            >
              <Eye size={13} />
              {resolved.preview_url ? ui('收起') : previewLoading ? ui('加载中') : ui('预览')}
            </button>
          )}
          <button
            type="button"
            title={ui('用默认应用打开')}
            aria-label={ui('打开文件 {name}', { name: file.name })}
            onClick={() => onOpen?.(file, 'open')}
            className="flex h-8 items-center gap-1 rounded-md border border-[#cdbca9] bg-white px-2 text-xs text-[#62462e] hover:bg-[#fffaf4]"
          >
            <ExternalLink size={13} />
            {ui('打开')}
          </button>
          {file.path && (
            <button
              type="button"
              title={ui('在文件管理器中显示')}
              aria-label={ui('打开文件所在目录 {name}', { name: file.name })}
              onClick={() => onOpen?.(file, 'reveal')}
              className="flex h-8 items-center gap-1 rounded-md border border-[#cdbca9] bg-white px-2 text-xs text-[#62462e] hover:bg-[#fffaf4]"
            >
              <FolderOpen size={13} />
              {ui('目录')}
            </button>
          )}
        </div>
      </div>
      <div className="mt-2 rounded-md bg-white/70 px-2 py-1.5 text-[11px] leading-4 text-[#67584c]">
        {locationLoading ? ui('正在读取本机路径…') : ui('已保存到本机')}
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
  const [open, setOpen] = useState(false);
  const toolRows = parseToolActivity(steps);
  const resultSteps = steps.filter((step) => step.phase === 'result');
  const hasFailure = resultSteps.some((step) => /未成功|失败|拒绝|错误|blocked|failed|denied/i.test(step.detail))
    || toolRows.some((row) => row.status === 'failed' || row.status === 'denied');
  const requestedBrowserNavigation = steps.some((step) => (
    step.phase === 'tool' && /plugin__browser_links__open_url/i.test(step.detail)
  ));
  const hasBrowserLaunchReceipt = resultSteps.some((step) => (
    /桌面启动器已确认|launcher_accepted|execution_receipt/i.test(step.detail)
  ));
  const unverifiedLegacyNavigation = (
    requestedBrowserNavigation
    && !hasBrowserLaunchReceipt
    && !hasFailure
  );
  const finalized = steps.some((step) => step.phase === 'final');
  const verifiedExecution = hasBrowserLaunchReceipt && !hasFailure;
  const needsReview = hasFailure || unverifiedLegacyNavigation || (finalized && !verifiedExecution);
  if (!steps.length && !files.length) return null;

  const summary = unverifiedLegacyNavigation
    ? ui('历史外部导航缺少桌面执行凭证')
    : hasFailure
      ? ui('执行已结束，存在需检查的结果')
      : verifiedExecution
        ? ui('外部操作已核验完成')
        : toolRows.length
          ? ui('已记录工具结果；请在运行审计中确认核验状态')
          : finalized
            ? ui('回答已生成，尚未获得外部执行核验')
            : ui('已记录本次处理结果');

  return (
    <section
      aria-label={ui('实际执行与交付')}
      data-execution-status={
        unverifiedLegacyNavigation
          ? 'unverified'
          : hasFailure
            ? 'failed'
            : verifiedExecution
              ? 'verified'
              : finalized
                ? 'unverified'
                : 'recorded'
      }
      className="mt-3"
    >
      <div className={`flex items-center gap-2 px-1 py-1 text-[13px] ${needsReview ? 'text-[#8a4b2a]' : 'text-[#6b6259]'}`}>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <ChevronRight size={14} className={`shrink-0 transition ${open ? 'rotate-90' : ''}`} />
          <span className="min-w-0 flex-1 truncate">{summary}</span>
          {files.length > 0 && (
            <span className="shrink-0 text-[11px] text-[#8b8176]">{ui('交付文件')} {files.length}</span>
          )}
          {resultSteps.length > 0 && (
            <span className="sr-only">{ui('结果核对')} {resultSteps.length}</span>
          )}
        </button>
        {onOpenRunHistory && (
          <button
            type="button"
            onClick={onOpenRunHistory}
            className="shrink-0 text-[12px] text-[#8d5f35] hover:underline"
          >
            {ui('查看运行审计')}
          </button>
        )}
      </div>
      {unverifiedLegacyNavigation && (
        <p className="mt-1 pl-6 text-[12px] leading-5 text-[#8a4b2a]">
          {ui('该历史记录只证明生成了打开链接意图，不能证明浏览器已启动；请重新发起该操作。')}
        </p>
      )}
      {open && (
        <div className="mt-1 space-y-1 pl-6">
          {toolRows.map((row) => (
            <div key={`${row.name}-${row.status}`} className="text-[12px] text-[#5b5147]">
              <span className="text-[#8b8176]">{row.status === 'failed' ? 'Failed' : row.status === 'denied' ? 'Denied' : 'Used'}</span>
              {' '}
              <span className="font-medium">{row.label}</span>
            </div>
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
  onApprovalDecision,
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
  const { t, ui } = useI18n();

  useEffect(() => {
    const scroller = scrollRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, [approvals.length, visibleMessages.length]);

  const isEmpty = visibleMessages.length === 0;

  return (
    <div ref={scrollRef} className={`aether-chat-scroll flex-1 overflow-auto px-4 ${isEmpty ? 'flex flex-col' : 'py-6'}`}>
      <h1 className="sr-only">{t('feedTitle')}</h1>
      <p className="sr-only">{t('feedDescription')}</p>
      <div className={`mx-auto w-full max-w-2xl ${isEmpty ? 'm-auto' : 'space-y-6'}`}>
        {isEmpty && (
          <ChatEmptyState
            hasConfiguredModel={hasConfiguredModel}
            hasLocalModel={hasLocalModel}
            onPickExample={(prompt) => onPickExample?.(prompt)}
          />
        )}

        {visibleMessages.map((msg) => {
          const isUser = msg.role === 'user' || msg.author_model === 'user';
          return (
          <article key={msg.id} className={isUser ? 'flex justify-end' : 'block'}>
            {isUser ? (
              <div className="max-w-[min(36rem,85%)] rounded-2xl bg-[#ece4d8] px-4 py-2.5 text-[15px] leading-6 text-[#2f2a24]">
                <SafeMarkdown content={msg.content} />
                {msg.attachments && msg.attachments.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {msg.attachments.map((attachment) => (
                      <span key={attachment.id} className="inline-flex max-w-48 items-center gap-1 rounded-md bg-white/70 px-1.5 py-0.5 text-[11px] text-[#5b4532]" title={t('chunks', { count: attachment.chunk_count })}>
                        <FileText size={12} className="shrink-0" />
                        <span className="truncate">{attachment.name}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div>
                <div className="mb-1.5 text-[12px] text-[#8b8176]">{msg.author_model}</div>
                <div className="text-[15px] leading-7 text-[#2f2a24]">
                  <SafeMarkdown content={msg.content} />
                </div>
                <ExecutionReceipt
                  steps={msg.reasoning_summary || []}
                  files={msg.generated_files || []}
                  onOpenRunHistory={onOpenRunHistory}
                />
                {visibleGeneratedFiles(msg.generated_files).length > 0 && (
                  <div className="mt-3 space-y-2" aria-label={ui('本次生成的文件')}>
                    <div className="text-[11px] font-semibold text-[#796b5e]">
                      {ui('本次生成的文件')}
                    </div>
                    {visibleGeneratedFiles(msg.generated_files).map((file, index) => (
                      <GeneratedFileCard
                        key={`${file.path || file.url || file.name}-${index}`}
                        file={file}
                        onInspect={onInspectGeneratedFile}
                        onResolve={onResolveGeneratedFile}
                        onOpen={onOpenGeneratedFile}
                      />
                    ))}
                    {technicalGeneratedFiles(msg.generated_files).length > 0 && (
                      <details className="text-[11px] text-[#8b8176]">
                        <summary className="cursor-pointer">{ui('技术细节')}</summary>
                        <ul className="mt-1 list-disc pl-4">
                          {technicalGeneratedFiles(msg.generated_files).map((file) => (
                            <li key={file.path || file.name}>{displayGeneratedFileName(file)}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                )}
              </div>
            )}
          </article>
          );
        })}

        {approvals.filter((item) => !item.decision).slice(0, 1).map((approval) => (
          onApprovalDecision
            ? (
              <ApprovalPrompt
                key={approval.id}
                approval={approval}
                queuedCount={Math.max(0, approvals.filter((item) => !item.decision).length - 1)}
                onDecision={onApprovalDecision}
              />
            )
            : null
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
              className="px-1 py-1"
              aria-label={t('approvalLabel')}
            >
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[13px]">
                <span className={approval.decision === 'deny' ? 'text-[#8f352c]' : 'text-[#3d6b4a]'}>
                  {decisionLabel}
                </span>
                <span className="text-[#6b6259]">{approval.tool_name}</span>
              </div>
              {approval.feedback && (
                <div className="mt-1 text-[12px] text-[#6f3029]">
                  {t('approvalFeedbackSummary', { feedback: approval.feedback })}
                </div>
              )}
            </article>
          );
        })}

      </div>
    </div>
  );
};
