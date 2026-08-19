import { useEffect, useState } from 'react';
import { useDialogFocus } from './useDialogFocus';

export type ConfirmOptions = {
  title?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
};

type ConfirmRequest = ConfirmOptions & {
  id: number;
  message: string;
  resolve: (value: boolean) => void;
};

const listeners = new Set<(request: ConfirmRequest | null) => void>();
let nextId = 1;

export function confirmAction(message: string, options: ConfirmOptions = {}): Promise<boolean> {
  return new Promise((resolve) => {
    const request: ConfirmRequest = {
      id: nextId,
      message,
      resolve,
      ...options,
    };
    nextId += 1;
    if (!listeners.size) {
      resolve(window.confirm(message));
      return;
    }
    listeners.forEach((listener) => listener(request));
  });
}

export function ConfirmHost() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  useEffect(() => {
    const listener = (next: ConfirmRequest | null) => setRequest(next);
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  }, []);
  const close = (value: boolean) => {
    request?.resolve(value);
    setRequest(null);
  };
  const dialogFocus = useDialogFocus<HTMLElement>(Boolean(request), () => close(false));
  if (!request) return null;
  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-black/30 px-4"
      role="presentation"
      onMouseDown={() => close(false)}
    >
      <section
        ref={dialogFocus.containerRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={`confirm-title-${request.id}`}
        aria-describedby={`confirm-body-${request.id}`}
        className="w-full max-w-md rounded-xl border border-[#d8cfc3] bg-[#fbfaf7] p-4 shadow-2xl"
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={dialogFocus.onKeyDown}
      >
        <h2 id={`confirm-title-${request.id}`} className="text-sm font-semibold text-[#2f2a24]">
          {request.title || '请确认'}
        </h2>
        <p id={`confirm-body-${request.id}`} className="mt-2 text-sm leading-6 text-[#5c534b]">
          {request.message}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="h-8 rounded-md border border-[#cfc4b7] px-3 text-xs text-[#5c534b]"
            onClick={() => close(false)}
          >
            {request.cancelLabel || '取消'}
          </button>
          <button
            type="button"
            data-dialog-initial-focus
            className={`h-8 rounded-md px-3 text-xs text-white ${request.danger ? 'bg-[#8a3028]' : 'bg-[#2f2a24]'}`}
            onClick={() => close(true)}
          >
            {request.confirmLabel || '确定'}
          </button>
        </div>
      </section>
    </div>
  );
}
