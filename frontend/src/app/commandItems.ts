import type { CommandItem } from '../components/chrome';
import type { Artifact, Conversation, ModelConfig, Task } from '../types';

const SETTINGS_PANELS = [
  '记忆', '资料', '任务', '自动化', '工件', '合并', '工具', '插件',
  '连接', '电脑', '模型', '运行', '设置', '语音', '规范', '治理',
] as const;

export function buildCommandItems(input: {
  conversations: Conversation[];
  tasks: Task[];
  artifacts: Artifact[];
  modelConfigs: ModelConfig[];
  ui: (key: string) => string;
  setActivePanel: (panel: typeof SETTINGS_PANELS[number]) => void;
  selectConversation: (id: string) => void;
  setSelectedArtifactPath: (path: string) => void;
  startVoice: () => void;
}): CommandItem[] {
  const panels = SETTINGS_PANELS.map((panel) => ({
    id: `panel-${panel}`,
    label: `打开${panel}面板`,
    hint: '命令',
    run: () => input.setActivePanel(panel),
  }));
  return [
    {
      id: 'voice-live',
      label: input.ui('开始语音对话'),
      hint: input.ui('本地 · Live'),
      run: () => { input.startVoice(); },
    },
    ...panels,
    ...input.conversations.map((conversation) => ({
      id: `conversation-${conversation.id}`,
      label: conversation.title,
      hint: `会话 · ${conversation.summary}`,
      run: () => input.selectConversation(conversation.id),
    })),
    ...input.tasks.map((task) => ({
      id: `task-${task.id}`,
      label: task.title,
      hint: `任务 · ${task.status}`,
      run: () => input.setActivePanel('任务'),
    })),
    ...input.artifacts.map((artifact) => ({
      id: `artifact-${artifact.path}`,
      label: artifact.path,
      hint: `工件 · ${artifact.kind}`,
      run: () => {
        input.setSelectedArtifactPath(artifact.path);
        input.setActivePanel('工件');
      },
    })),
    ...input.modelConfigs.map((model) => ({
      id: `model-${model.name}`,
      label: model.name,
      hint: `模型 · ${model.model}`,
      run: () => input.setActivePanel('模型'),
    })),
  ];
}
