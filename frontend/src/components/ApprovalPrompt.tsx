import { useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from '../i18n';
import type { ApprovalRequest } from '../types';
import { compactToolName } from './toolDisplay';
import { Button } from '../ui';

type ApprovalDecision = NonNullable<ApprovalRequest['decision']>;

interface ApprovalPromptProps {
  approval: ApprovalRequest;
  queuedCount?: number;
  onDecision: (
    approval: ApprovalRequest,
    decision: ApprovalDecision,
    feedback?: string,
  ) => void | Promise<void>;
}

interface ApprovalOption {
  decision: ApprovalDecision;
  label: string;
}

export const ApprovalPrompt = ({
  approval,
  queuedCount = 0,
  onDecision,
}: ApprovalPromptProps) => {
  const { t } = useI18n();
  const menuRef = useRef<HTMLDivElement>(null);
  const feedbackRef = useRef<HTMLInputElement>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [feedbackMode, setFeedbackMode] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const options = useMemo<ApprovalOption[]>(() => [
    {
      decision: 'allow_once',
      label: t('approvalAllowOnceChoice'),
    },
    ...(approval.allow_session
      ? [{
        decision: 'allow_session' as const,
        label: t('approvalAllowSessionChoice', { tool: approval.tool_name }),
      }]
      : []),
    {
      decision: 'deny',
      label: t('approvalDenyChoice'),
    },
  ], [approval.allow_session, approval.tool_name, t]);

  useEffect(() => {
    setSelectedIndex(0);
    setFeedbackMode(false);
    setFeedback('');
    setSubmitting(false);
    const frame = window.requestAnimationFrame(() => menuRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [approval.id]);

  useEffect(() => {
    if (!feedbackMode) return undefined;
    const frame = window.requestAnimationFrame(() => feedbackRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [feedbackMode]);

  const submit = async (decision: ApprovalDecision, rejectionFeedback = '') => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onDecision(
        approval,
        decision,
        decision === 'deny' ? rejectionFeedback.trim() : undefined,
      );
    } finally {
      setSubmitting(false);
    }
  };

  const activate = (option: ApprovalOption) => {
    if (option.decision === 'deny') {
      setFeedbackMode(true);
      return;
    }
    void submit(option.decision);
  };

  const moveSelection = (offset: number) => {
    setSelectedIndex((current) => (
      (current + offset + options.length) % options.length
    ));
  };

  const returnToMenu = () => {
    setFeedbackMode(false);
    setSelectedIndex(options.length - 1);
    window.requestAnimationFrame(() => menuRef.current?.focus());
  };

  const toolLabel = compactToolName(approval.tool_name);

  return (
    <article
      aria-label={t('approvalLabel')}
      className="rounded-card border border-border bg-paper-raised px-4 py-3 shadow-card"
    >
      <div className="min-w-0">
        <h2 className="text-sm font-medium text-[#2f2a24]">
          {t('approvalPromptHeading', { tool: toolLabel || approval.tool_name })}
        </h2>
        {approval.title && (
          <p className="mt-1 text-sm leading-5 text-[#5b5147]">{approval.title}</p>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-[#7a7065]">
          <span>{t('approvalRequest', { model: approval.model, tool: toolLabel || approval.tool_name })}</span>
          {approval.risk && (
            <>
              <span aria-hidden="true">·</span>
              <span>{t('approvalRisk', { risk: approval.risk })}</span>
            </>
          )}
          {queuedCount > 0 && (
            <>
              <span aria-hidden="true">·</span>
              <span>{t('approvalQueued', { count: queuedCount })}</span>
            </>
          )}
        </div>
      </div>

      <div className="mt-3">
        {approval.detail && (
          <pre className="max-h-24 overflow-auto whitespace-pre-wrap rounded-lg bg-[#f6f0e8] px-3 py-2 font-mono text-[12px] leading-5 text-[#4d4034]">
            {approval.detail}
          </pre>
        )}
        <details className="mt-2 text-[12px] text-[#7a7065]">
          <summary className="w-fit cursor-pointer select-none">{t('redactedArgs')}</summary>
          <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg bg-[#f6f0e8] p-2 font-mono text-[11px] leading-5">
            {JSON.stringify(approval.arguments, null, 2)}
          </pre>
        </details>

        {!feedbackMode ? (
          <>
            <div className="sr-only">{t('approvalChoose')}</div>
            <div
              ref={menuRef}
              role="listbox"
              tabIndex={0}
              aria-label={t('approvalLabel')}
              aria-activedescendant={`approval-option-${approval.id}-${selectedIndex}`}
              className="mt-3 flex flex-wrap gap-2 outline-none"
              onKeyDown={(event) => {
                if (submitting) return;
                if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                  event.preventDefault();
                  moveSelection(1);
                } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                  event.preventDefault();
                  moveSelection(-1);
                } else if (event.key === 'Home') {
                  event.preventDefault();
                  setSelectedIndex(0);
                } else if (event.key === 'End') {
                  event.preventDefault();
                  setSelectedIndex(options.length - 1);
                } else if (/^[1-9]$/.test(event.key)) {
                  const index = Number(event.key) - 1;
                  if (index < options.length) {
                    event.preventDefault();
                    setSelectedIndex(index);
                  }
                } else if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  activate(options[selectedIndex]);
                } else if (event.key === 'Escape') {
                  event.preventDefault();
                  setSelectedIndex(options.length - 1);
                  setFeedbackMode(true);
                }
              }}
            >
              {options.map((option, index) => {
                const selected = index === selectedIndex;
                const danger = option.decision === 'deny';
                return (
                  <Button
                    id={`approval-option-${approval.id}-${index}`}
                    key={option.decision}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    tabIndex={-1}
                    disabled={submitting}
                    onClick={() => activate(option)}
                    size="sm"
                    variant={danger ? (selected ? 'danger' : 'ghost') : (selected ? 'primary' : 'secondary')}
                    className={`rounded-pill ${selected ? '' : ''} ${danger && !selected ? 'text-danger' : ''}`}
                  >
                    {option.label}
                  </Button>
                );
              })}
            </div>
            <div className="mt-2 text-[11px] text-[#8b8176]">
              {submitting ? t('approvalSubmitting') : t('approvalKeyboardHint')}
            </div>
          </>
        ) : (
          <div className="mt-3 rounded-lg border border-[#d8c3a7] bg-white p-3">
            <label htmlFor={`approval-feedback-${approval.id}`} className="block text-xs font-semibold text-[#4f3c2c]">
              {t('approvalFeedbackLabel')}
            </label>
            <input
              ref={feedbackRef}
              id={`approval-feedback-${approval.id}`}
              value={feedback}
              maxLength={2000}
              disabled={submitting}
              onChange={(event) => setFeedback(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void submit('deny', feedback);
                } else if (event.key === 'Escape') {
                  event.preventDefault();
                  returnToMenu();
                }
              }}
              placeholder={t('approvalFeedbackPlaceholder')}
              className="mt-2 h-10 w-full rounded-md border border-[#cdb99f] bg-[#fffdf9] px-3 text-sm text-[#2f2a24] outline-none placeholder:text-[#7d7064]"
            />
            <div className="mt-2 flex items-center justify-between gap-3">
              <span className="text-[11px] text-[#6f6257]">
                {submitting ? t('approvalSubmitting') : t('approvalFeedbackHint')}
              </span>
              <div className="flex shrink-0 gap-2">
                <Button type="button" size="sm" variant="secondary" disabled={submitting} onClick={returnToMenu}>
                  {t('approvalBack')}
                </Button>
                <Button type="button" size="sm" variant="danger" disabled={submitting} onClick={() => void submit('deny', feedback)}>
                  {t('approvalDenySubmit')}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </article>
  );
};
