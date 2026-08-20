import type { CommandItem } from '../components/chrome';
import { SETTINGS_TABS } from '../components/settingsTabs';
import type { TranslationKey } from '../i18n';
import type { Artifact, Conversation, ModelConfig, Task } from '../types';

const SETTINGS_PANELS = SETTINGS_TABS;

export function buildCommandItems(input: {
  conversations: Conversation[];
  tasks: Task[];
  artifacts: Artifact[];
  modelConfigs: ModelConfig[];
  t: (key: TranslationKey, values?: Record<string, string | number>) => string;
  setActivePanel: (panel: typeof SETTINGS_PANELS[number]) => void;
  selectConversation: (id: string) => void;
  setSelectedArtifactPath: (path: string) => void;
  startVoice: () => void;
}): CommandItem[] {
  const panels = SETTINGS_PANELS.map((panel) => ({
    id: `panel-${panel}`,
    label: input.t('openPanel', { panel: input.t(panel) }),
    hint: input.t('command'),
    run: () => input.setActivePanel(panel),
  }));
  return [
    {
      id: 'voice-live',
      label: input.t('startVoiceChat'),
      hint: input.t('localLive'),
      run: () => { input.startVoice(); },
    },
    ...panels,
    ...input.conversations.map((conversation) => ({
      id: `conversation-${conversation.id}`,
      label: conversation.title,
      hint: input.t('conversationHint', { summary: conversation.summary }),
      run: () => input.selectConversation(conversation.id),
    })),
    ...input.tasks.map((task) => ({
      id: `task-${task.id}`,
      label: task.title,
      hint: input.t('taskHint', { status: task.status }),
      run: () => input.setActivePanel('tasks'),
    })),
    ...input.artifacts.map((artifact) => ({
      id: `artifact-${artifact.path}`,
      label: artifact.path,
      hint: input.t('artifactHint', { kind: artifact.kind }),
      run: () => {
        input.setSelectedArtifactPath(artifact.path);
        input.setActivePanel('artifacts');
      },
    })),
    ...input.modelConfigs.map((model) => ({
      id: `model-${model.name}`,
      label: model.name,
      hint: input.t('modelHint', { model: model.model }),
      run: () => input.setActivePanel('models'),
    })),
  ];
}
