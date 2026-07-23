import { useEffect, useRef, type KeyboardEvent } from 'react';

const focusableSelector = [
  '[data-dialog-initial-focus]', 'button:not([disabled])', '[href]', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',');

export function useDialogFocus<T extends HTMLElement>(open: boolean, onClose: () => void) {
  const containerRef = useRef<T>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = requestAnimationFrame(() => {
      const container = containerRef.current;
      const preferred = container?.querySelector<HTMLElement>('[data-dialog-initial-focus]');
      const first = container?.querySelector<HTMLElement>(focusableSelector);
      (preferred || first || container)?.focus();
    });
    return () => {
      cancelAnimationFrame(frame);
      const target = previousFocus.current;
      if (target?.isConnected) requestAnimationFrame(() => target.focus());
      previousFocus.current = null;
    };
  }, [open]);

  const onKeyDown = (event: KeyboardEvent<T>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const nodes = Array.from(containerRef.current?.querySelectorAll<HTMLElement>(focusableSelector) || [])
      .filter((item) => !item.hasAttribute('disabled') && item.getClientRects().length > 0);
    if (!nodes.length) {
      event.preventDefault();
      containerRef.current?.focus();
      return;
    }
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return { containerRef, onKeyDown };
}
