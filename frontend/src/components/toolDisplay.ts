import type { ReasoningStep } from '../types';

export function compactToolName(toolName?: string): string {
  const value = String(toolName || '').trim();
  if (!value) return '';
  const segments = value.split('__').filter(Boolean);
  return segments[segments.length - 1] || value;
}

export type ToolActivityRow = {
  name: string;
  label: string;
  detail: string;
  status: 'used' | 'failed' | 'denied';
};

export function parseToolActivity(steps: ReasoningStep[]): ToolActivityRow[] {
  const rows: ToolActivityRow[] = [];
  const seen = new Set<string>();
  for (const step of steps) {
    if (step.phase !== 'tool' && step.phase !== 'result') continue;
    const named = step.detail.match(/(?:已选择调用|工具)\s+([A-Za-z0-9_.:-]+)/);
    const raw = named?.[1] || '';
    if (!raw) continue;
    const label = compactToolName(raw);
    const key = `${label}:${step.phase}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const failed = /失败|错误|blocked|failed|denied|拒绝/i.test(step.detail);
    rows.push({
      name: raw,
      label,
      detail: step.detail,
      status: /拒绝|denied/i.test(step.detail) ? 'denied' : failed ? 'failed' : 'used',
    });
  }
  const unique: ToolActivityRow[] = [];
  const byLabel = new Set<string>();
  for (const row of [...rows].reverse()) {
    if (byLabel.has(row.label)) continue;
    byLabel.add(row.label);
    unique.push(row);
  }
  return unique.reverse();
}
