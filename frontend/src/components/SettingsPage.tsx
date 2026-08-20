import type { ReactNode } from 'react';
import { useI18n } from '../i18n';
import { SETTINGS_GROUPS, type SettingsTabId } from './settingsTabs';

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
  return (
    <div className="flex min-h-0 flex-1 bg-[#f4eee6]" data-testid="settings-page">
      <aside className="flex w-40 shrink-0 flex-col border-r border-[#d8cfc3] bg-[#efe7dc]">
        <div className="flex items-center justify-between border-b border-[#d8cfc3] px-3 py-2">
          <h1 className="text-xs font-semibold">{t('settings')}</h1>
          <button
            type="button"
            onClick={onBackToChat}
            className="h-6 rounded px-1.5 text-[11px] text-[#5f554d] hover:bg-[#e7ddd0]"
          >
            {t('backToChat')}
          </button>
        </div>
        <nav
          role="tablist"
          aria-label={t('settingsGroups')}
          className="min-h-0 flex-1 overflow-auto p-1.5"
        >
          {SETTINGS_GROUPS.map((group) => (
            <div key={group.id} className="mb-2">
              <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-[#8b8176]">
                {t(group.label)}
              </div>
              {group.tabs.map((id) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={activeTab === id}
                  onClick={() => onChangeTab(id)}
                  className={`mb-0.5 flex h-7 w-full items-center rounded-md px-2 text-left text-xs ${
                    activeTab === id
                      ? 'bg-[#2f2a24] font-medium text-white'
                      : 'text-[#5b4532] hover:bg-[#e7ddd0]'
                  }`}
                >
                  {t(id)}
                </button>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <div
        className="aether-settings-content min-h-0 min-w-0 flex-1 overflow-y-auto"
        data-testid="settings-content"
      >
        {children}
      </div>
    </div>
  );
}
