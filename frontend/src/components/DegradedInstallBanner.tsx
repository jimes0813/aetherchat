import { useState } from 'react';
import { useI18n } from '../i18n';

const ACK_KEY = 'aetherchat.degraded-install-banner.ack';

function isDegradedRuntime() {
  return window.aetherRuntime?.chromiumSandbox === 'off'
    || window.aetherRuntime?.installChannel === 'user-unpacked'
    || window.aetherRuntime?.installChannel === 'user-appimage';
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
      className="fixed left-0 right-0 top-0 z-[80] flex items-start justify-between gap-3 border-b border-[#e2b8b0] bg-[#fff5f2] px-4 py-2 text-xs leading-5 text-[#8a4036]"
    >
      <p>
        {ui('当前是用户本地安装，Chromium 沙箱已关闭（降级模式）。这适合本机 RC，不适合发给他人。系统 Deb（/opt/AetherChat）才会启用 setuid 沙箱。')}
      </p>
      <button
        type="button"
        onClick={dismiss}
        className="shrink-0 rounded border border-[#e2b8b0] bg-white px-2 py-1 text-[11px] text-[#8a4036]"
      >
        {ui('知道了')}
      </button>
    </div>
  );
}
