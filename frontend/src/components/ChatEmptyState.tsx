import { Sparkles } from 'lucide-react';
import { useI18n } from '../i18n';

export type EmptyExample = {
  id: string;
  title: string;
  prompt: string;
  hint: string;
};

export const DEFAULT_EMPTY_EXAMPLES: EmptyExample[] = [
  {
    id: 'intro',
    title: '快速认识工作台',
    prompt: '用三句话介绍你能帮我做什么，并建议我接下来怎么配置模型。',
    hint: '适合首次连通后验证回复',
  },
  {
    id: 'plan',
    title: '拆解一个小任务',
    prompt: '请把“整理本周待办并排出优先级”拆成 5 个可执行步骤，每步不超过 20 字。',
    hint: '练习目标→计划表达',
  },
  {
    id: 'code',
    title: '写一段可运行片段',
    prompt: '用 Python 写一个函数：输入字符串列表，返回去重后按长度排序的结果，并给一个简短示例。',
    hint: '验证模型写代码能力',
  },
];

export const LOCAL_EMPTY_EXAMPLE: EmptyExample = {
  id: 'local-hello',
  title: '用本地模型打个招呼',
  prompt: '你好，请用一句话确认你是本机本地模型，并说明当前能帮我做什么。',
  hint: '验证本地 start→verify 后的真实回复',
};

export type ChatEmptyStateProps = {
  hasConfiguredModel: boolean;
  hasLocalModel?: boolean;
  examples?: EmptyExample[];
  onPickExample: (prompt: string) => void;
};

export function ChatEmptyState({
  hasConfiguredModel,
  hasLocalModel = false,
  examples,
  onPickExample,
}: ChatEmptyStateProps) {
  const { t, ui } = useI18n();
  const resolvedExamples = examples ?? (
    hasLocalModel
      ? [LOCAL_EMPTY_EXAMPLE, ...DEFAULT_EMPTY_EXAMPLES.slice(0, 2)]
      : DEFAULT_EMPTY_EXAMPLES
  );

  return (
    <div className="px-2 py-8">
      <div className="mx-auto max-w-xl text-center">
        <div className="text-[1.65rem] font-medium tracking-tight text-[#2f2a24]">{t('emptyTitle')}</div>
        <div className="mt-2 text-sm leading-6 text-[#7a7065]">{t('emptyDescription')}</div>
        {!hasConfiguredModel && (
          <p className="mt-4 text-sm text-[#8a5a28]">
            {ui('还没有可用模型。请先在「模型」面板配置密钥，或完成首次设置向导。')}
          </p>
        )}
        {hasConfiguredModel && hasLocalModel && (
          <p className="mt-4 text-sm text-[#326022]">
            {ui('已检测到本地模型配置。若回复失败，请到「模型」面板使用「启动并验证」唤醒本机服务。')}
          </p>
        )}
      </div>

      <div className="mx-auto mt-8 flex max-w-xl flex-wrap justify-center gap-2">
        {resolvedExamples.map((example) => (
          <button
            key={example.id}
            type="button"
            disabled={!hasConfiguredModel}
            onClick={() => onPickExample(example.prompt)}
            className="inline-flex items-center gap-1.5 rounded-full border border-[#e1d9cd] bg-white px-3 py-1.5 text-sm text-[#4a4037] transition hover:border-[#c9a27a] hover:bg-[#fffaf4] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Sparkles size={13} className="text-[#8d5f35]" />
            {ui(example.title)}
          </button>
        ))}
      </div>
    </div>
  );
}
