import type { ReactNode } from 'react';
import { useI18n } from '../i18n';
import {
  SETTINGS_NAV_TABS,
  isWorkspaceTab,
  settingsNavLabelKey,
  type SettingsTabId,
} from './settingsTabs';

type SettingsPageProps = {
  activeTab: SettingsTabId;
  onChangeTab: (tab: SettingsTabId) => void;
  onBackToChat: () => void;
  children: ReactNode;
};

export function SettingsPage({
  activeTab,
  onChangeTab,
  onBackToChat,
  children,
}: SettingsPageProps) {
  const { t } = useI18n();
  const navTabs = isWorkspaceTab(activeTab)
    ? [...SETTINGS_NAV_TABS, activeTab]
    : [...SETTINGS_NAV_TABS];
  return (
    <div className="flex min-h-0 flex-1 bg-[#f7f7f8]" data-testid="settings-page">
      <aside className="flex w-56 shrink-0 flex-col border-r border-[#ececec] bg-white">
        <div className="flex items-center justify-between px-4 py-3">
          <h1 className="text-sm font-semibold tracking-tight text-[#0d0d0d]">{t('settings')}</h1>
          <button
            type="button"
            onClick={onBackToChat}
            className="h-7 rounded-md px-2 text-xs text-[#6e6e6e] hover:bg-[#f4f4f4]"
          >
            {t('backToChat')}
          </button>
        </div>
        <nav
          role="tablist"
          aria-label={t('settingsGroups')}
          className="min-h-0 flex-1 overflow-auto px-2 pb-3"
        >
          {navTabs.map((id) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={activeTab === id}
              onClick={() => onChangeTab(id)}
              className={`mb-0.5 flex h-9 w-full items-center rounded-lg px-3 text-left text-sm ${
                activeTab === id
                  ? 'bg-[#ececec] font-medium text-[#0d0d0d]'
                  : 'text-[#3d3d3d] hover:bg-[#f4f4f4]'
              }`}
            >
              {t(settingsNavLabelKey(id))}
            </button>
          ))}
        </nav>
      </aside>
      <div
        className="aether-settings-content min-h-0 min-w-0 flex-1 overflow-y-auto bg-white"
        data-testid="settings-content"
      >
        {children}
      </div>
    </div>
  );
}
