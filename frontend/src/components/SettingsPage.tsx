import type { ReactNode } from 'react';
import { useI18n } from '../i18n';
import { useDialogFocus } from './useDialogFocus';
import {
  navGroupForPanel,
  visibleSettingsNav,
  type SettingsTabId,
} from './settingsTabs';

type SettingsPageProps = {
  activeTab: SettingsTabId;
  onChangeTab: (tab: SettingsTabId) => void;
  onBackToChat: () => void;
  children: ReactNode;
  developerMode?: boolean;
};

export function SettingsPage({
  activeTab,
  onChangeTab,
  onBackToChat,
  children,
  developerMode = false,
}: SettingsPageProps) {
  const { t } = useI18n();
  const dialogFocus = useDialogFocus<HTMLDivElement>(true, onBackToChat);
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-3"
      role="presentation"
      onMouseDown={onBackToChat}
    >
      <div
        ref={dialogFocus.containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('settings')}
        data-testid="settings-page"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={dialogFocus.onKeyDown}
        className="flex h-[min(42rem,calc(100vh-1.5rem))] w-[min(56rem,calc(100vw-1.5rem))] overflow-hidden rounded-2xl border border-[#ececec] bg-white shadow-2xl"
      >
        <aside className="flex w-56 shrink-0 flex-col border-r border-[#ececec] bg-[#f7f7f8]">
          <div className="flex items-center justify-between px-4 py-3">
            <h1 className="text-sm font-semibold tracking-tight text-[#0d0d0d]">{t('settings')}</h1>
            <button
              type="button"
              data-dialog-initial-focus
              onClick={onBackToChat}
              className="h-7 rounded-md px-2 text-xs text-[#6e6e6e] hover:bg-[#ececec]"
            >
              {t('backToChat')}
            </button>
          </div>
          <nav
            role="tablist"
            aria-label={t('settingsGroups')}
            className="min-h-0 flex-1 overflow-auto px-2 pb-3"
          >
            {visibleSettingsNav(developerMode).map((group) => {
              const selected = (group.panels as readonly string[]).includes(activeTab);
              return (
                <button
                  key={group.id}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  onClick={() => onChangeTab(group.panels[0])}
                  className={`mb-0.5 flex h-9 w-full items-center rounded-lg px-3 text-left text-sm ${
                    selected
                      ? 'bg-white font-medium text-[#0d0d0d] shadow-sm'
                      : 'text-[#3d3d3d] hover:bg-[#ececec]'
                  }`}
                >
                  {t(group.label)}
                </button>
              );
            })}
          </nav>
        </aside>
        <div
          className="aether-settings-content min-h-0 min-w-0 flex-1 overflow-y-auto bg-white"
          data-testid="settings-content"
        >
          {navGroupForPanel(activeTab).panels.length > 1 && (
            <nav aria-label={t('settingsSubsections')} className="sticky top-0 z-10 flex flex-wrap gap-1 border-b border-[#ececec] bg-white px-4 py-2">
              {navGroupForPanel(activeTab).panels.map((panel) => (
                <button
                  key={panel}
                  type="button"
                  aria-current={panel === activeTab ? 'page' : undefined}
                  onClick={() => onChangeTab(panel)}
                  className={`rounded-md px-2.5 py-1.5 text-xs ${panel === activeTab ? 'bg-[#2f2a24] text-white' : 'bg-[#f3f0ec] text-[#5d5147]'}`}
                >
                  {t(panel)}
                </button>
              ))}
            </nav>
          )}
          {children}
        </div>
      </div>
    </div>
  );
}
