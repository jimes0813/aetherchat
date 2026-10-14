import { useState } from 'react';
import { useI18n } from '../i18n';

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
    <div
      role="status"
      className="relative z-10 flex shrink-0 items-start justify-between gap-3 border-b border-[#e2b8b0] bg-[#fff5f2] px-4 py-2 text-xs leading-5 text-[#8a4036]"
    >
      <p>
        {ui('Chromium 沙箱已关闭（降级模式）。当前安装通道：{channel}。请重新安装正式 Deb；在此之前不要处理不可信内容。', {
          channel: window.aetherRuntime?.installChannel || 'unknown',
        })}
      </p>
      <button
        type="button"
        onClick={dismiss}
        className="rounded border border-[#e2b8b0] bg-white px-2 py-1 text-[11px] text-[#8a4036]"
      >
        {ui('知道了')}
      </button>
    </div>
  );
}
