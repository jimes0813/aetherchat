import { useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n';

export type AgentActivityItem = {
  model: string;
  phase: 'preparing' | 'requesting' | 'streaming' | 'thinking' | 'tool' | 'waiting' | 'running' | 'finalizing';
  toolName?: string;
  startedAt: number;
  updatedAt?: number;
  detail?: string;
  contextMessages?: number;
  contextTokens?: number;
  toolCount?: number;
  toolSchemaTokens?: number;
  roundIndex?: number;
  firstTokenMs?: number;
};

interface ModelWorkingStripProps {
  activity: Record<string, AgentActivityItem>;
}

const compactToolName = (toolName?: string) => {
  const value = String(toolName || '').trim();
  if (!value) return '';
  const segments = value.split('__').filter(Boolean);
  return segments[segments.length - 1] || value;
};

export function ModelWorkingStrip({ activity }: ModelWorkingStripProps) {
  const { locale } = useI18n();
  const [now, setNow] = useState(Date.now());
  const activeAgents = useMemo(() => (
    Object.values(activity).sort((left, right) => (
      (right.updatedAt || right.startedAt) - (left.updatedAt || left.startedAt)
    ))
  ), [activity]);
  const primary = activeAgents[0];

  useEffect(() => {
    if (!primary) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [primary?.model, primary?.startedAt]);

  if (!primary) return null;

  const isEnglish = locale === 'en-US';
  const toolName = compactToolName(primary.toolName);
  const phaseLabel = primary.phase === 'tool'
    ? (isEnglish ? `using ${toolName || 'a tool'}` : `正在使用 ${toolName || '工具'}`)
    : primary.phase === 'waiting'
      ? (isEnglish ? 'waiting for your approval' : '等待你的授权')
      : primary.phase === 'running'
        ? (isEnglish ? 'running the plan' : '正在执行计划')
        : primary.phase === 'finalizing'
          ? (isEnglish ? 'finishing the response' : '正在整理结果')
          : primary.phase === 'preparing'
            ? (isEnglish ? 'preparing context' : '正在准备')
            : primary.phase === 'streaming'
              ? (isEnglish ? 'writing the response' : '正在组织答复')
              : (isEnglish ? 'working' : '正在思考');
  const elapsedSeconds = Math.max(0, Math.floor((now - primary.startedAt) / 1000));
  const elapsedLabel = elapsedSeconds < 60
    ? (isEnglish ? `${elapsedSeconds}s` : `${elapsedSeconds} 秒`)
    : (isEnglish
        ? `${Math.floor(elapsedSeconds / 60)}m ${elapsedSeconds % 60}s`
        : `${Math.floor(elapsedSeconds / 60)} 分 ${elapsedSeconds % 60} 秒`);
  const modelLabel = `${primary.model}${activeAgents.length > 1 ? ` +${activeAgents.length - 1}` : ''}`;
  const accessibleLabel = `${modelLabel} · ${phaseLabel} · ${elapsedLabel}`;

  return (
    <div className="border-t border-[#e7dfd3] bg-[#fbfaf7] px-6 py-2">
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        aria-label={accessibleLabel}
        data-model-working="true"
        data-phase={primary.phase}
        className="aether-working-strip mx-auto flex max-w-3xl items-center gap-2 text-xs text-[#665e56]"
      >
        <span className="aether-working-dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span className="min-w-0 flex-1 truncate">
          <span className="font-semibold text-[#403a34]">{modelLabel}</span>
          <span> · {phaseLabel}</span>
        </span>
        <span className="shrink-0 tabular-nums text-[#7a7168]">{elapsedLabel}</span>
      </div>
    </div>
  );
}
