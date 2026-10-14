import type { ReactNode } from 'react';
import { useDialogFocus } from '../components/useDialogFocus';
import { Button } from './Button';

export interface DialogProps {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
  closeLabel?: string;
  initialFocusClose?: boolean;
}

export function Dialog({
  open,
  title,
  children,
  onClose,
  closeLabel = 'Close',
  initialFocusClose = true,
}: DialogProps) {
  const { containerRef, onKeyDown } = useDialogFocus<HTMLDivElement>(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4" role="presentation">
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className="w-full max-w-lg rounded-card border border-border bg-paper-raised p-4 shadow-card outline-none"
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            data-dialog-initial-focus={initialFocusClose ? true : undefined}
            aria-label={closeLabel}
          >
            {closeLabel}
          </Button>
        </div>
        <div className="text-sm text-ink-soft">{children}</div>
      </div>
    </div>
  );
}
