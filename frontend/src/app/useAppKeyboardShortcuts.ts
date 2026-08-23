import { useEffect } from 'react';
import type { SettingsTabId } from '../components/settingsTabs';

export function useAppKeyboardShortcuts(input: {
  activePanel: SettingsTabId | null;
  voiceOpen: boolean;
  setActivePanel: (panel: SettingsTabId | null) => void;
  setCommandOpen: (open: boolean) => void;
}) {
  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        input.setCommandOpen(true);
      }
      if ((event.ctrlKey || event.metaKey) && event.key === ',') {
        event.preventDefault();
        input.setActivePanel(input.activePanel ?? 'settings');
      }
      if (event.key === 'Escape' && !input.voiceOpen) {
        input.setCommandOpen(false);
        input.setActivePanel(null);
      }
    };
    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, [input.activePanel, input.setActivePanel, input.setCommandOpen, input.voiceOpen]);
}
