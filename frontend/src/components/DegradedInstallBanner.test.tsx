import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DegradedInstallBanner } from './DegradedInstallBanner';

const ACK_KEY = 'aetherchat.degraded-install-banner.ack';

beforeEach(() => {
  window.localStorage.clear();
  window.aetherRuntime = {
    apiBase: 'http://127.0.0.1:8000',
    backendStatus: 'ready',
    chromiumSandbox: 'off',
    installChannel: 'user-unpacked',
  };
});

describe('DegradedInstallBanner', () => {
  it('explains user-unpacked Chromium sandbox off and can be dismissed', () => {
    render(<DegradedInstallBanner />);
    expect(screen.getByRole('status').textContent).toMatch(/降级模式/);
    fireEvent.click(screen.getByRole('button', { name: '知道了' }));
    expect(screen.queryByRole('status')).toBeNull();
    expect(window.localStorage.getItem(ACK_KEY)).toBe('1');
  });

  it('stays hidden after acknowledgement and on sandboxed installs', () => {
    window.localStorage.setItem(ACK_KEY, '1');
    const first = render(<DegradedInstallBanner />);
    expect(first.queryByRole('status')).toBeNull();
    first.unmount();
    window.localStorage.clear();
    window.aetherRuntime = {
      apiBase: 'http://127.0.0.1:8000',
      backendStatus: 'ready',
      chromiumSandbox: 'on',
      installChannel: 'system-deb',
    };
    const second = render(<DegradedInstallBanner />);
    expect(second.queryByRole('status')).toBeNull();
  });
});
