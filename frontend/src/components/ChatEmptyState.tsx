import { MessageSquarePlus, PlayCircle, Sparkles } from 'lucide-react';
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
  onOpenModels?: () => void;
  onOpenRuns?: () => void;
};

export function ChatEmptyState({
  hasConfiguredModel,
  hasLocalModel = false,
  examples,
  onPickExample,
  onOpenModels,
  onOpenRuns,
}: ChatEmptyStateProps) {
  const { t } = useI18n();
  const resolvedExamples = examples ?? (
    hasLocalModel
      ? [LOCAL_EMPTY_EXAMPLE, ...DEFAULT_EMPTY_EXAMPLES.slice(0, 2)]
      : DEFAULT_EMPTY_EXAMPLES
  );

  return (
    <div className="rounded-xl border border-dashed border-[#d6cbbd] bg-[#fffdf9] px-5 py-8">
      <div className="mx-auto max-w-xl text-center">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-[#ebe3d8] text-[#5b4532]">
          <MessageSquarePlus size={18} />
        </div>
        <div className="text-sm font-medium text-[#4a4037]">{t('emptyTitle')}</div>
        <div className="mt-2 text-sm leading-6 text-[#6b6259]">{t('emptyDescription')}</div>
        {!hasConfiguredModel && (
          <p className="mt-3 rounded-lg bg-[#fff5ec] px-3 py-2 text-xs text-[#8a5a28]">
            还没有可用模型。请先在「模型」面板配置密钥，或完成首次设置向导。
          </p>
        )}
        {hasConfiguredModel && hasLocalModel && (
          <p className="mt-3 rounded-lg bg-[#eef6ea] px-3 py-2 text-xs text-[#326022]">
            已检测到本地模型配置。若回复失败，请到「模型」面板使用「启动并验证」唤醒本机服务。
          </p>
        )}
      </div>

      <div className="mx-auto mt-6 grid max-w-2xl gap-3 sm:grid-cols-3">
        {resolvedExamples.map((example) => (
          <button
            key={example.id}
            type="button"
            disabled={!hasConfiguredModel}
            onClick={() => onPickExample(example.prompt)}
            className="rounded-xl border border-[#e1d9cd] bg-white p-3 text-left shadow-sm transition hover:border-[#c9a27a] hover:bg-[#fffaf4] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <div className="flex items-center gap-2 text-sm font-medium text-[#4a4037]">
              <Sparkles size={14} className="text-[#8d5f35]" />
              {example.title}
            </div>
            <p className="mt-2 line-clamp-3 text-xs leading-5 text-[#6b6259]">{example.prompt}</p>
            <p className="mt-2 text-[11px] text-[#8b8176]">{example.hint}</p>
          </button>
        ))}
      </div>

      <div className="mx-auto mt-5 flex max-w-2xl flex-wrap justify-center gap-2">
        {onOpenModels && (
          <button
            type="button"
            onClick={onOpenModels}
            className="rounded-lg border border-[#d8cfc3] bg-white px-3 py-1.5 text-xs text-[#5d4a3a] hover:bg-[#f5efe7]"
          >
            打开模型面板
          </button>
        )}
        {onOpenRuns && (
          <button
            type="button"
            onClick={onOpenRuns}
            className="inline-flex items-center gap-1 rounded-lg border border-[#d8cfc3] bg-white px-3 py-1.5 text-xs text-[#5d4a3a] hover:bg-[#f5efe7]"
          >
            <PlayCircle size={13} />
            查看运行记录
          </button>
        )}
      </div>
    </div>
  );
}
