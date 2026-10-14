import { useState } from 'react';
import { useI18n } from '../i18n';
import { Banner } from '../ui';

const ACK_KEY = 'aetherchat.degraded-install-banner.ack';

function isDegradedRuntime() {
  // Banner only when Chromium sandbox is actually off (e.g. explicit --no-sandbox).
  return window.aetherRuntime?.chromiumSandbox === 'off';
}

export function DegradedInstallBanner() {
  const { ui } = useI18n();
  const [hidden, setHidden] = useState(() => {
    try {
      return window.localStorage.getItem(ACK_KEY) === '1';
    } catch {
      return false;
    }
  });
  if (!isDegradedRuntime() || hidden) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(ACK_KEY, '1');
    } catch {
      // localStorage may be unavailable in locked-down sessions.
    }
    setHidden(true);
  };

  return (
    <Banner
      tone="warning"
      role="status"
      actionLabel={ui('知道了')}
      onAction={dismiss}
    >
      <p>
        {ui('Chromium 沙箱已关闭（降级模式）。当前安装通道：{channel}。请重新安装正式 Deb；在此之前不要处理不可信内容。', {
          channel: window.aetherRuntime?.installChannel || 'unknown',
        })}
      </p>
    </Banner>
  );
}
