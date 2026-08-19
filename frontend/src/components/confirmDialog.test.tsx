import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ConfirmHost, confirmAction } from './confirmDialog';

describe('confirmAction', () => {
  it('resolves true when the user confirms', async () => {
    render(<ConfirmHost />);
    const pending = confirmAction('删除这个会话？', { danger: true });
    expect(await screen.findByRole('alertdialog')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await expect(pending).resolves.toBe(true);
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
  });

  it('resolves false when the user cancels', async () => {
    render(<ConfirmHost />);
    const pending = confirmAction('继续？');
    fireEvent.click(await screen.findByRole('button', { name: '取消' }));
    await expect(pending).resolves.toBe(false);
  });
});
