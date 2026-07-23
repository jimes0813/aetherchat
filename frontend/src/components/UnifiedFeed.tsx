import { useEffect, useMemo, useRef } from 'react';
import { Bot, Check, Clock3, FileText, ShieldAlert, User, X } from 'lucide-react';
import { useSessionStore } from '../stores/sessionStore';
import type { ApprovalRequest } from '../types';
import { SafeMarkdown } from './SafeMarkdown';
import { useI18n } from '../i18n';

interface UnifiedFeedProps {
  approvals?: ApprovalRequest[];
  onApprovalDecision?: (approval: ApprovalRequest, decision: 'allow_once' | 'allow_session' | 'deny') => void;
}

export const UnifiedFeed = ({ approvals = [], onApprovalDecision }: UnifiedFeedProps) => {
  const { messages, streams } = useSessionStore();
  const streamEntries = useMemo(() => Object.entries(streams), [streams]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { locale, t } = useI18n();

  useEffect(() => {
    const scroller = scrollRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, [messages.length, streamEntries.length, streams]);

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

        {messages.length === 0 && streamEntries.length === 0 && (
          <div className="rounded-lg border border-dashed border-[#d6cbbd] bg-[#fffdf9] px-5 py-12 text-center">
            <div className="text-sm font-medium text-[#4a4037]">{t('emptyTitle')}</div>
            <div className="mt-2 text-sm text-[#6b6259]">{t('emptyDescription')}</div>
          </div>
        )}

        {messages.map((msg) => (
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
          </article>
        ))}

        {approvals.map((approval) => {
          const pending = !approval.decision;
          const decisionLabel = approval.decision === 'deny'
            ? t('denied')
            : approval.decision === 'allow_session'
              ? t('sessionAllowed')
              : approval.decision === 'allow_once'
                ? t('onceAllowed')
                : '';
          const titleId = `approval-title-${approval.id}`;
          return (
            <article key={approval.id} className="rounded-lg border-2 border-[#9a5b20] bg-[#fff8ec] p-4 shadow-sm" aria-label={t('approvalLabel')}>
              <div className="flex items-start gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#9a5b20] text-white"><ShieldAlert size={18} /></div>
                <div className="min-w-0 flex-1">
                  <div id={titleId} className="text-sm font-semibold text-[#5e3717]">{approval.title}</div>
                  <div className="mt-1 text-xs text-[#684a2e]">{t('approvalRequest', { model: approval.model, tool: approval.tool_name })}</div>
                  <div className="mt-1 text-xs font-medium text-[#684a2e]">{t('approvalRisk', { risk: approval.risk })}</div>
                  <div className="mt-1 text-xs text-[#684a2e]">{t('approvalScope')}</div>
                  {approval.detail && <pre className="mt-3 max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-[#f4e6d1] p-2 text-xs leading-5 text-[#5c4935]">{approval.detail}</pre>}
                  <details className="mt-2 text-xs text-[#71583e]">
                    <summary className="cursor-pointer">{t('redactedArgs')}</summary>
                    <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-[#f4e6d1] p-2">{JSON.stringify(approval.arguments, null, 2)}</pre>
                  </details>
                  {pending ? (
                    <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label={t('approvalLabel')}>
                      <button onClick={() => onApprovalDecision?.(approval, 'deny')} className="inline-flex items-center gap-1 rounded-md border-2 border-[#97382c] bg-white px-3 py-2 text-xs font-semibold text-[#7c291f]"><X size={14} />{t('deny')}</button>
                      {approval.allow_session && <button onClick={() => onApprovalDecision?.(approval, 'allow_session')} className="rounded-md border border-[#815d3b] bg-white px-3 py-2 text-xs font-semibold text-[#533516]">{t('allowSession')}</button>}
                      <button onClick={() => onApprovalDecision?.(approval, 'allow_once')} className="inline-flex items-center gap-1 rounded-md bg-[#2f2a24] px-3 py-2 text-xs font-semibold text-white"><Check size={14} />{t('allowOnce')}</button>
                    </div>
                  ) : (
                    <div aria-live="polite" className={`mt-3 text-xs font-semibold ${approval.decision === 'deny' ? 'text-[#97382c]' : 'text-[#24613c]'}`}>{decisionLabel}</div>
                  )}
                </div>
              </div>
            </article>
          );
        })}

        {streamEntries.map(([model, content]) => (
          <article key={model} className="rounded-lg border border-[#d7c8b8] bg-[#f5ede2] p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-[#5b4532]">
              <Bot size={16} />
              {t('streaming', { model })}
            </div>
            <SafeMarkdown content={content} />
          </article>
        ))}
      </div>
    </div>
  );
};
