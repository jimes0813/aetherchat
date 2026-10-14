import type { ReactNode } from 'react';
import { Button } from './Button';

export type BannerTone = 'warning' | 'danger' | 'info' | 'success';

const toneClass: Record<BannerTone, string> = {
  warning: 'border-danger-border bg-danger-soft text-danger',
  danger: 'border-danger-border bg-danger-soft text-danger',
  info: 'border-border bg-accent-soft text-ink-soft',
  success: 'border-border bg-success-soft text-success',
};

export interface BannerProps {
  tone?: BannerTone;
  children: ReactNode;
  actionLabel?: string;
  onAction?: () => void;
  role?: 'status' | 'alert';
}

export function Banner({
  tone = 'info',
  children,
  actionLabel,
  onAction,
  role = 'status',
}: BannerProps) {
  return (
    <div
      role={role}
      className={`relative z-10 flex shrink-0 items-start justify-between gap-3 border-b px-4 py-2 text-xs leading-5 ${toneClass[tone]}`}
    >
      <div className="min-w-0 flex-1">{children}</div>
      {actionLabel && onAction ? (
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={onAction}
          className="shrink-0 border-danger-border bg-paper-raised text-danger"
        >
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}
