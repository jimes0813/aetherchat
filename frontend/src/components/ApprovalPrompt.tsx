import { useEffect, useMemo, useRef, useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import { useI18n } from '../i18n';
import type { ApprovalRequest } from '../types';

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

  return (
    <article
      aria-label={t('approvalLabel')}
      className="max-h-[min(30rem,58vh)] overflow-y-auto rounded-xl border-2 border-[#9a5b20] bg-[#fffaf1] shadow-[0_12px_34px_rgba(90,55,25,0.16)]"
    >
      <div className="flex items-start gap-3 border-b border-[#ead7bd] px-4 py-3.5">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#9a5b20] text-white">
          <ShieldAlert size={18} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-[#533516]">
            {t('approvalPromptHeading')}
          </div>
          <h2 className="mt-1 text-sm font-medium leading-5 text-[#2f2a24]">
            {approval.title}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[#684a2e]">
            <span>{t('approvalRequest', { model: approval.model, tool: approval.tool_name })}</span>
            <span aria-hidden="true">·</span>
            <span>{t('approvalRisk', { risk: approval.risk })}</span>
            {queuedCount > 0 && (
              <>
                <span aria-hidden="true">·</span>
                <span>{t('approvalQueued', { count: queuedCount })}</span>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="px-4 py-3.5">
        {approval.detail && (
          <pre className="max-h-28 overflow-auto whitespace-pre-wrap rounded-lg border border-[#e3d5c2] bg-[#f4e9d9] px-3 py-2 font-mono text-xs leading-5 text-[#4d4034]">
            {approval.detail}
          </pre>
        )}
        <details className="mt-2 text-[11px] text-[#71583e]">
          <summary className="w-fit cursor-pointer select-none">{t('redactedArgs')}</summary>
          <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded-lg border border-[#e3d5c2] bg-[#f4e9d9] p-2 font-mono text-xs leading-5">
            {JSON.stringify(approval.arguments, null, 2)}
          </pre>
        </details>

        {!feedbackMode ? (
          <>
            <div className="mb-2 mt-3 text-xs font-semibold text-[#5e4a37]">
              {t('approvalChoose')}
            </div>
            <div
              ref={menuRef}
              role="listbox"
              tabIndex={0}
              aria-label={t('approvalLabel')}
              aria-activedescendant={`approval-option-${approval.id}-${selectedIndex}`}
              className="space-y-1 rounded-lg outline-none"
              onKeyDown={(event) => {
                if (submitting) return;
                if (event.key === 'ArrowDown') {
                  event.preventDefault();
                  moveSelection(1);
                } else if (event.key === 'ArrowUp') {
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
                return (
                  <button
                    id={`approval-option-${approval.id}-${index}`}
                    key={option.decision}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    tabIndex={-1}
                    disabled={submitting}
                    onClick={() => activate(option)}
                    className={`grid w-full grid-cols-[1rem_1.25rem_minmax(0,1fr)] items-start gap-1 rounded-md px-2.5 py-2 text-left text-xs leading-5 transition ${
                      selected
                        ? 'bg-[#2f2a24] font-semibold text-white'
                        : 'text-[#4f4338] hover:bg-[#f0e3d1]'
                    } disabled:cursor-wait disabled:opacity-60`}
                  >
                    <span aria-hidden="true" className="text-center">{selected ? '›' : ''}</span>
                    <span aria-hidden="true">{index + 1}.</span>
                    <span>{option.label}</span>
                  </button>
                );
              })}
            </div>
            <div className="mt-2 text-[11px] text-[#6f6257]">
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
                <button
                  type="button"
                  disabled={submitting}
                  onClick={returnToMenu}
                  className="rounded-md border border-[#cdb99f] bg-white px-2.5 py-1.5 text-xs font-semibold text-[#5a4633] disabled:opacity-60"
                >
                  {t('approvalBack')}
                </button>
                <button
                  type="button"
                  disabled={submitting}
                  onClick={() => void submit('deny', feedback)}
                  className="rounded-md bg-[#8f352c] px-2.5 py-1.5 text-xs font-semibold text-white disabled:cursor-wait disabled:opacity-60"
                >
                  {t('approvalDenySubmit')}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </article>
  );
};
