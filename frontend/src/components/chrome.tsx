import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlarmClock,
  Archive,
  BookMarked,
  Bot,
  Brain,
  Cable,
  ChevronRight,
  CircleDollarSign,
  Cpu,
  FileCode2,
  FolderOpen,
  GitBranch,
  GitMerge,
  Laptop,
  LayoutList,
  ListTodo,
  Maximize2,
  MessageSquare,
  Minus,
  Plus,
  Puzzle,
  Radio,
  Search,
  Settings,
  SquareTerminal,
  Trash2,
  Users,
  Wrench,
  X,
} from 'lucide-react';
import { useDialogFocus } from './useDialogFocus';
import { API_BASE, formatDateTime, taskStatusLabel } from './shared';
import { fetchJson } from '../api/client';
import { useI18n } from '../i18n';
import { useSessionStore } from '../stores/sessionStore';
import type { Conversation, Task, TaskEvent, WorkspaceSettings } from '../types';

export type CommandItem = {
  id: string;
  label: string;
  hint: string;
  run: () => void;
};

type SessionSearchHit = {
  message_id: string;
  session_id: string;
  author_model: string;
  role: string;
  content: string;
  snippet: string;
  created_at: string;
  title: string;
  updated_at: string;
};

export function DesktopTitleBar() {
  const platformLabel = window.aetherRuntime?.platform === 'linux'
    ? 'Linux'
    : window.aetherRuntime?.platform === 'darwin'
      ? 'macOS'
      : window.aetherRuntime?.platform === 'win32'
        ? 'Windows'
        : '浏览器';
  const windowActions = [
    { label: '最小化', icon: Minus, action: () => window.aetherWindow?.minimize() },
    { label: '最大化', icon: Maximize2, action: () => window.aetherWindow?.toggleMaximize() },
    {
      label: '关闭',
      icon: X,
      action: () => {
        if (window.aetherWindow) window.aetherWindow.close();
        else window.close();
      },
    },
  ];

  return (
    <header className="grid h-10 grid-cols-[auto_minmax(0,1fr)_auto] items-center bg-[#f5efe7] text-[#4a4037]">
      <div className="flex h-full items-center gap-2 px-3">
        <div className="flex h-6 w-6 items-center justify-center rounded-md bg-[#2f2a24] text-white">
          <Radio size={13} />
        </div>
        <span className="text-xs font-semibold">AetherChat Desktop</span>
        <span className="ml-2 text-[11px] text-[#8b8176]">{platformLabel}</span>
      </div>
      <div className="flex h-full items-center text-[12px] text-[#6d6258]">
        前端 + 本机后端一体运行
      </div>
      <div className="flex h-full items-center">
        {windowActions.map(({ label, icon: Icon, action }) => (
          <button key={label} title={label} onClick={action} className="flex h-10 w-11 items-center justify-center text-[#6d6258] hover:bg-[#e2d9ce]">
            <Icon size={14} />
          </button>
        ))}
      </div>
    </header>
  );
}

export const ACTIVITY_RAIL_ADVANCED_KEY = 'aetherchat.activity_rail.advanced.v1';

interface ActivityRailProps {
  activePanel: ReturnType<typeof useSessionStore.getState>['activePanel'];
  setActivePanel: ReturnType<typeof useSessionStore.getState>['setActivePanel'];
}

type RailItem = {
  label: string;
  icon: typeof MessageSquare;
  panel: ReturnType<typeof useSessionStore.getState>['activePanel'];
  group: 'primary' | 'advanced';
};

export function ActivityRail({ activePanel, setActivePanel }: ActivityRailProps) {
  const { t, ui } = useI18n();
  const [showAdvanced, setShowAdvanced] = useState(() => {
    try {
      return globalThis.localStorage?.getItem(ACTIVITY_RAIL_ADVANCED_KEY) === '1';
    } catch {
      return false;
    }
  });

  const items: RailItem[] = [
    { label: t('chat'), icon: MessageSquare, panel: null, group: 'primary' },
    { label: t('models'), icon: Cpu, panel: '模型', group: 'primary' },
    { label: t('runs'), icon: SquareTerminal, panel: '运行', group: 'primary' },
    { label: t('settings'), icon: Settings, panel: '设置', group: 'primary' },
    { label: t('memory'), icon: Brain, panel: '记忆', group: 'advanced' },
    { label: t('knowledge'), icon: FolderOpen, panel: '资料', group: 'advanced' },
    { label: t('tasks'), icon: LayoutList, panel: '任务', group: 'advanced' },
    { label: t('automations'), icon: AlarmClock, panel: '自动化', group: 'advanced' },
    { label: t('artifacts'), icon: FileCode2, panel: '工件', group: 'advanced' },
    { label: t('changes'), icon: GitMerge, panel: '合并', group: 'advanced' },
    { label: t('tools'), icon: Wrench, panel: '工具', group: 'advanced' },
    { label: t('plugins'), icon: Puzzle, panel: '插件', group: 'advanced' },
    { label: t('connections'), icon: Cable, panel: '连接', group: 'advanced' },
    { label: t('computer'), icon: Laptop, panel: '电脑', group: 'advanced' },
    { label: t('instructions'), icon: BookMarked, panel: '规范', group: 'advanced' },
  ];

  const primary = items.filter((item) => item.group === 'primary');
  const advanced = items.filter((item) => item.group === 'advanced');
  const advancedSelected = advanced.some((item) => item.panel === activePanel);
  const visible = showAdvanced || advancedSelected
    ? [...primary, ...advanced]
    : primary;

  const toggleAdvanced = () => {
    const next = !showAdvanced;
    setShowAdvanced(next);
    try {
      globalThis.localStorage?.setItem(ACTIVITY_RAIL_ADVANCED_KEY, next ? '1' : '0');
    } catch {
      // ignore storage failures in restricted contexts
    }
  };

  return (
    <aside className="flex min-h-0 flex-col items-center border-r border-[#d8cfc3] bg-[#2f2a24] py-3 text-[#d8cfc3]">
      <div className="mb-4 flex h-9 w-9 items-center justify-center rounded-lg bg-[#f5efe7] text-[#2f2a24]">
        <Bot size={17} />
      </div>
      <div className="flex flex-1 flex-col gap-2">
        {visible.map((item) => {
          const Icon = item.icon;
          const selected = item.panel === activePanel;
          return (
            <button
              key={item.label}
              title={item.label}
              aria-label={item.label}
              aria-pressed={selected}
              onClick={() => setActivePanel(item.panel)}
              className={`flex h-10 w-10 items-center justify-center rounded-lg transition ${selected ? 'bg-[#8d5f35] text-white' : 'hover:bg-[#443b32]'}`}
            >
              <Icon size={18} />
            </button>
          );
        })}
      </div>
      <button
        type="button"
        title={showAdvanced ? ui('收起高级面板') : ui('更多面板')}
        aria-label={showAdvanced ? ui('收起高级面板') : ui('更多面板')}
        aria-expanded={showAdvanced || advancedSelected}
        onClick={toggleAdvanced}
        className="mt-2 flex h-10 w-10 items-center justify-center rounded-lg text-[10px] font-medium hover:bg-[#443b32]"
      >
        {showAdvanced || advancedSelected ? '收起' : '更多'}
      </button>
    </aside>
  );
}

interface WorkspaceSidebarProps {
  conversations: ReturnType<typeof useSessionStore.getState>['conversations'];
  currentSessionId: string;
  onNewConversation: () => void;
  onOpenSearch: () => void;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
}

interface MainViewTabsProps {
  value: 'chat' | 'history' | 'tasks';
  onChange: (value: MainViewTabsProps['value']) => void;
}

export function MainViewTabs({ value, onChange }: MainViewTabsProps) {
  const { t, ui } = useI18n();
  const tabs = [
    { id: 'chat' as const, label: t('currentChat'), icon: MessageSquare },
    { id: 'history' as const, label: t('chatHistory'), icon: Archive },
    { id: 'tasks' as const, label: t('taskRecords'), icon: ListTodo },
  ];
  return (
    <nav role="tablist" aria-label={ui('会话视图')} className="flex h-11 shrink-0 items-end gap-1 border-b border-[#ddd3c7] bg-[#fbfaf7] px-4">
      {tabs.map((tab) => {
        const Icon = tab.icon;
        const selected = tab.id === value;
        return (
          <button
            key={tab.id}
            type="button"
            aria-selected={selected}
            role="tab"
            onClick={() => onChange(tab.id)}
            className={`flex h-10 items-center gap-2 border-b-2 px-3 text-sm ${selected ? 'border-[#8d5f35] font-medium text-[#5d3b20]' : 'border-transparent text-[#766b60] hover:text-[#2f2a24]'}`}
          >
            <Icon size={15} />
            {tab.label}
          </button>
        );
      })}
    </nav>
  );
}

interface ConversationHistoryProps {
  conversations: Conversation[];
  currentSessionId: string;
  onContinue: (id: string) => void;
  onDelete: (id: string) => void;
}

export function ConversationHistory({ conversations, currentSessionId, onContinue, onDelete }: ConversationHistoryProps) {
  const { ui } = useI18n();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SessionSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  useEffect(() => {
    const normalized = query.trim();
    if (!normalized) {
      setResults([]);
      setSearching(false);
      return undefined;
    }
    let cancelled = false;
    setSearching(true);
    const timer = window.setTimeout(() => {
      fetchJson<SessionSearchHit[]>(
        `${API_BASE}/sessions/search?q=${encodeURIComponent(normalized)}&limit=100`,
      )
        .then((items) => {
          if (!cancelled) setResults(items);
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);
  return (
    <div className="min-h-0 flex-1 overflow-auto bg-[#fbfaf7] px-5 py-4">
      <div className="mx-auto max-w-4xl">
        <div className="mb-4 flex items-baseline justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold">{ui('历史聊天')}</h2>
            <p className="mt-1 text-xs text-[#7a7065]">{ui('选择会话后恢复消息、任务和工作区状态')}</p>
          </div>
          <span className="text-xs text-[#8b8176]">{ui('{count} 个会话', { count: conversations.length })}</span>
        </div>
        <label className="mb-4 flex h-10 items-center gap-2 rounded-lg border border-[#d8cfc3] bg-white px-3">
          <Search size={15} className="text-[#776b60]" />
          <span className="sr-only">{ui('搜索全部聊天记录')}</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={ui('搜索全部聊天内容、会话标题或 Agent')}
            className="min-w-0 flex-1 bg-transparent text-sm outline-none"
          />
          {searching && <span className="text-[11px] text-[#776b60]">{ui('搜索中')}</span>}
        </label>
        {query.trim() && (
          <div className="mb-5 divide-y divide-[#e3dbd0] border-y border-[#e3dbd0]" data-testid="chat-search-results">
            {results.map((result) => (
              <button
                key={result.message_id}
                type="button"
                onClick={() => onContinue(result.session_id)}
                className="block w-full px-2 py-3 text-left hover:bg-[#f5efe7]"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="truncate text-sm font-medium">{result.title}</span>
                  <time className="shrink-0 text-[11px] text-[#81756a]">{formatDateTime(result.created_at)}</time>
                </div>
                <div className="mt-1 text-[11px] font-medium text-[#755333]">{result.author_model}</div>
                <p className="mt-1 line-clamp-3 text-xs leading-5 text-[#62584f]">{result.snippet}</p>
              </button>
            ))}
            {!searching && !results.length && (
              <div className="py-10 text-center text-sm text-[#8b8176]">{ui('没有匹配的聊天记录')}</div>
            )}
          </div>
        )}
        {!query.trim() && (
        <div className="divide-y divide-[#e3dbd0] border-y border-[#e3dbd0]">
          {conversations.map((conversation) => (
            <div key={conversation.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-stretch">
              <button
                type="button"
                onClick={() => onContinue(conversation.id)}
                className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-4 px-2 py-4 text-left hover:bg-[#f5efe7]"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">{conversation.title}</span>
                    {conversation.id === currentSessionId && <span className="shrink-0 rounded bg-[#e7d8c8] px-1.5 py-0.5 text-[10px] text-[#6d4828]">{ui('当前')}</span>}
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-[#6f655b]">{conversation.summary}</p>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[#93887d]">
                    <span>{ui('{count} 条消息', { count: conversation.messageCount ?? 0 })}</span>
                    <span>{ui('{count} 个任务', { count: conversation.taskCount ?? 0 })}</span>
                    <span>{ui('{count} 个进行中', { count: conversation.openTaskCount ?? 0 })}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2 self-center text-xs text-[#8b8176]">
                  <span>{conversation.updatedAt}</span>
                  <ChevronRight size={15} />
                </div>
              </button>
              <button
                type="button"
                aria-label={ui('删除会话 {title}', { title: conversation.title })}
                title={ui('删除会话')}
                onClick={() => onDelete(conversation.id)}
                className="my-3 mr-2 flex w-9 items-center justify-center rounded-md text-[#815b43] hover:bg-[#f0dfd5] hover:text-[#8f2f25]"
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
          {!conversations.length && <div className="py-12 text-center text-sm text-[#8b8176]">{ui('暂无历史会话')}</div>}
        </div>
        )}
      </div>
    </div>
  );
}

interface TaskHistoryProps {
  tasks: Task[];
  events: TaskEvent[];
}

export function TaskHistory({ tasks, events }: TaskHistoryProps) {
  const { ui } = useI18n();
  return (
    <div className="min-h-0 flex-1 overflow-auto bg-[#fbfaf7] px-5 py-4">
      <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section>
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-base font-semibold">{ui('当前任务')}</h2>
            <span className="text-xs text-[#8b8176]">{ui('{count} 项', { count: tasks.length })}</span>
          </div>
          <div className="divide-y divide-[#e3dbd0] border-y border-[#e3dbd0]">
            {tasks.map((task) => (
              <div key={task.id} className="px-2 py-3">
                <div className="flex items-start justify-between gap-3">
                  <span className="text-sm font-medium leading-5">{task.title}</span>
                  <span className="shrink-0 rounded bg-[#ece4d9] px-2 py-0.5 text-[11px] text-[#665a4e]">{ui(taskStatusLabel[task.status])}</span>
                </div>
                {task.description && <p className="mt-1 text-xs leading-5 text-[#74695e]">{task.description}</p>}
                {task.claimed_by && <div className="mt-2 text-[11px] text-[#93887d]">{ui('执行者：{name}', { name: task.claimed_by })}</div>}
                {!!task.depends_on?.length && (
                  <div className="mt-1 text-[11px] text-[#93887d]">
                    依赖 {task.depends_on.length} 项
                    {task.blocked_by?.length ? ` · 仍阻塞 ${task.blocked_by.length} 项` : ' · 已满足'}
                  </div>
                )}
              </div>
            ))}
            {!tasks.length && <div className="py-10 text-center text-sm text-[#8b8176]">{ui('当前会话暂无任务')}</div>}
          </div>
        </section>
        <section>
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-base font-semibold">{ui('变更记录')}</h2>
            <span className="text-xs text-[#8b8176]">{ui('{count} 条', { count: events.length })}</span>
          </div>
          <div className="border-l border-[#cdbfae] pl-4">
            {events.map((event) => (
              <div key={event.id} className="relative pb-5">
                <span className="absolute -left-[19px] top-1.5 h-2 w-2 rounded-full bg-[#8d5f35]" />
                <div className="flex items-start justify-between gap-3">
                  <span className="text-sm font-medium">{event.task.title}</span>
                  <time className="shrink-0 text-[11px] text-[#93887d]">{formatDateTime(event.created_at)}</time>
                </div>
                <p className="mt-1 text-xs text-[#74695e]">
                  {event.action === 'created' ? ui('创建任务') : ui('更新任务')} · {ui(taskStatusLabel[event.task.status])}
                  {event.task.claimed_by ? ` · ${event.task.claimed_by}` : ''}
                </p>
              </div>
            ))}
            {!events.length && <div className="py-10 text-center text-sm text-[#8b8176]">{ui('暂无任务变更记录')}</div>}
          </div>
        </section>
      </div>
    </div>
  );
}

export function WorkspaceSidebar({ conversations, currentSessionId, onNewConversation, onOpenSearch, onSelectConversation, onDeleteConversation }: WorkspaceSidebarProps) {
  const { ui } = useI18n();
  return (
    <aside className="flex min-h-0 flex-col bg-[#eee7de]">
      <div className="border-b border-[#d8cfc3] px-3 py-3">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold">AetherChat</div>
            <div className="text-xs text-[#7a7065]">{ui('多模型 Agent 工作台')}</div>
          </div>
          <button onClick={onNewConversation} aria-label={ui('新建会话')} title={ui('新建会话')} className="flex h-8 w-8 items-center justify-center rounded-lg border border-[#d8cfc3] bg-[#fbfaf7]">
            <Plus size={15} />
          </button>
        </div>
        <button onClick={onOpenSearch} className="flex h-9 w-full items-center gap-2 rounded-lg border border-[#d8cfc3] bg-[#fbfaf7] px-3 text-left text-sm text-[#7a7065]">
          <Search size={15} />
          {ui('搜索会话、文件或命令')}
        </button>
      </div>

      <div className="border-b border-[#d8cfc3] p-3">
        <div className="rounded-lg bg-[#2f2a24] p-3 text-white">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <GitBranch size={15} />
            aetherchat/main
          </div>
          <div className="mt-2 text-xs leading-5 text-[#d8cfc3]">{ui('项目协作 · 中文优先 · Docker 严格沙箱')}</div>
        </div>
      </div>

      <div className="px-3 pt-3 text-xs font-semibold text-[#8b8176]">{ui('最近会话')}</div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {conversations.map((conversation) => (
          <div
            key={conversation.id}
            className={`group relative mb-2 rounded-lg border shadow-sm ${conversation.id === currentSessionId ? 'border-[#8d5f35] bg-white' : 'border-[#ddd3c7] bg-[#fbfaf7]'}`}
          >
            <button
              onClick={() => onSelectConversation(conversation.id)}
              aria-current={conversation.id === currentSessionId ? 'page' : undefined}
              className="w-full p-3 pr-10 text-left"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium">{conversation.title}</span>
                <ChevronRight size={14} className="text-[#9a9084]" />
              </div>
              <div className="mt-1 line-clamp-2 text-xs leading-5 text-[#7a7065]">{conversation.summary}</div>
              <div className="mt-2 text-[11px] text-[#9a9084]">{conversation.updatedAt}</div>
            </button>
            <button
              type="button"
              aria-label={ui('删除会话 {title}', { title: conversation.title })}
              title={ui('删除会话')}
              onClick={() => onDeleteConversation(conversation.id)}
              className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-md bg-white/90 text-[#7b5a42] opacity-0 shadow-sm transition hover:text-[#962f25] focus:opacity-100 group-hover:opacity-100"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
    </aside>
  );
}

interface CommandHeaderProps {
  connection: ReturnType<typeof useSessionStore.getState>['connection'];
  providerCount: number;
  activeProviderCount: number;
  budgetRemaining: number;
  spendPct: number;
  mode: string;
  mergeRule: string;
  onOpenSearch: () => void;
}

export function CommandHeader({
  connection,
  providerCount,
  activeProviderCount,
  budgetRemaining,
  spendPct,
  mode,
  mergeRule,
  onOpenSearch,
}: CommandHeaderProps) {
  const { ui } = useI18n();
  return (
    <header className="flex h-14 items-center gap-3 border-b border-[#e7dfd3] bg-[#fbfaf7] px-4">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold">{ui('AetherChat 中文协作工作台')}</div>
        <div className="truncate text-xs text-[#7a7065]">{ui('模式：{mode} · 合并规则：{rule}', { mode: ui(mode), rule: ui(mergeRule) })}</div>
      </div>
      <button onClick={onOpenSearch} className="mx-auto hidden h-9 min-w-[18rem] max-w-xl flex-1 items-center gap-2 rounded-lg border border-[#d8cfc3] bg-white px-3 text-left text-xs text-[#8b8176] lg:flex">
        <Search size={14} />
        {ui('Ctrl+K 搜索命令、文件、模型或任务')}
      </button>
      <div className="ml-auto flex items-center gap-2">
        <div className="flex items-center gap-2 rounded-lg border border-[#e1d9cd] bg-white px-3 py-2 text-xs text-[#6d6258]">
          <Users size={15} />
          {ui('{active}/{total} 个模型已配置', { active: activeProviderCount, total: providerCount })}
        </div>
        <div className="flex min-w-44 items-center gap-3 rounded-lg border border-[#e1d9cd] bg-white px-3 py-2">
          <CircleDollarSign size={15} className="text-[#8d5f35]" />
          <div className="flex-1">
            <div className="flex justify-between text-[11px] text-[#7a7065]">
              <span>{ui('预算余额')}</span>
              <span>${budgetRemaining.toFixed(2)}</span>
            </div>
            <div className="mt-1 h-1.5 rounded-full bg-[#e8e1d7]">
              <div className="h-full rounded-full bg-[#8d5f35]" style={{ width: `${spendPct}%` }} />
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 rounded-lg bg-[#2f2a24] px-3 py-2 text-xs font-medium text-white">
          <Activity size={14} />
          {connection === 'online' ? ui('已连接') : connection === 'connecting' ? ui('连接中') : ui('离线')}
        </div>
      </div>
    </header>
  );
}

interface CommandPaletteProps {
  items: CommandItem[];
  onClose: () => void;
}

export function CommandPalette({ items, onClose }: CommandPaletteProps) {
  const { ui } = useI18n();
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const dialogFocus = useDialogFocus<HTMLElement>(true, onClose);
  const filteredItems = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('zh-CN');
    if (!needle) return items.slice(0, 12);
    return items
      .filter((item) => `${item.label} ${item.hint}`.toLocaleLowerCase('zh-CN').includes(needle))
      .slice(0, 20);
  }, [items, query]);

  useEffect(() => setSelectedIndex(0), [query]);

  const choose = (item?: CommandItem) => {
    if (!item) return;
    item.run();
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-start justify-center bg-black/25 px-4 pt-[12vh]" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogFocus.containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={ui('搜索命令、会话、任务、工件或模型')}
        className="w-full max-w-2xl overflow-hidden rounded-xl border border-[#d8cfc3] bg-[#fbfaf7] shadow-2xl"
        onKeyDown={dialogFocus.onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-[#e1d9cd] px-4">
          <Search size={17} className="text-[#7a7065]" />
          <input
            data-dialog-initial-focus
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault();
                setSelectedIndex((index) => Math.min(index + 1, Math.max(0, filteredItems.length - 1)));
              }
              if (event.key === 'ArrowUp') {
                event.preventDefault();
                setSelectedIndex((index) => Math.max(0, index - 1));
              }
              if (event.key === 'Enter') {
                event.preventDefault();
                choose(filteredItems[selectedIndex]);
              }
              if (event.key === 'Escape') {
                event.stopPropagation();
                onClose();
              }
            }}
            placeholder={ui('输入关键词，例如：任务、README、模型')}
            className="h-12 min-w-0 flex-1 bg-transparent text-sm outline-none"
          />
          <kbd className="rounded border border-[#d8cfc3] bg-[#eee7de] px-2 py-1 text-[10px] text-[#7a7065]">ESC</kbd>
        </div>
        <div className="max-h-[26rem] overflow-auto p-2">
          {filteredItems.map((item, index) => (
            <button
              key={item.id}
              onMouseEnter={() => setSelectedIndex(index)}
              onClick={() => choose(item)}
              className={`flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left ${selectedIndex === index ? 'bg-[#e8ddd0]' : 'hover:bg-[#f1e9df]'}`}
            >
              <span className="truncate text-sm font-medium">{item.label}</span>
              <span className="ml-4 truncate text-xs text-[#8b8176]">{item.hint}</span>
            </button>
          ))}
          {filteredItems.length === 0 && (
            <div className="px-3 py-8 text-center text-sm text-[#8b8176]">{ui('没有匹配结果')}</div>
          )}
        </div>
      </section>
    </div>
  );
}

interface DesktopStatusBarProps {
  connection: ReturnType<typeof useSessionStore.getState>['connection'];
  budgetRemaining: number;
  runQueuePosition?: number | null;
  onCancelQueuedRun?: () => void;
  uiLocale: WorkspaceSettings['uiLocale'];
  sandbox: WorkspaceSettings['sandbox'];
  tokenStatus: string;
  onRefreshToken: () => void;
  onToggleLanguage: () => void;
}

export function DesktopStatusBar({ connection, budgetRemaining, runQueuePosition, onCancelQueuedRun, uiLocale, sandbox, tokenStatus, onRefreshToken, onToggleLanguage }: DesktopStatusBarProps) {
  const { t, ui } = useI18n();
  return (
    <footer className="flex h-7 items-center gap-4 border-t border-[#cfc4b7] bg-[#2f2a24] px-3 text-[11px] text-[#e7dfd3]">
      <span className="flex items-center gap-1"><FolderOpen size={12} /> {ui('本地工作区')}</span>
      <span>{t('sandbox', { value: sandbox })}</span>
      <span>WS {connection === 'online' ? 'connected' : connection === 'connecting' ? 'connecting' : 'offline'}</span>
      {runQueuePosition !== undefined && (
        <button
          type="button"
          onClick={onCancelQueuedRun}
          aria-label={ui('取消排队运行')}
          title={ui('取消排队运行')}
          className="rounded bg-[#6f5843] px-1.5 py-0.5 text-[10px] text-white"
        >
          {runQueuePosition ? ui('运行排队 #{value} · 取消', { value: runQueuePosition }) : ui('运行排队 · 取消')}
        </button>
      )}
      <span>{t('budget', { value: budgetRemaining.toFixed(2) })}</span>
      <button onClick={onRefreshToken} title="刷新本地 WebSocket 授权，不显示或复制令牌" className="rounded bg-[#8d5f35] px-1.5 py-0.5 text-[10px] text-white">{tokenStatus}</button>
      <button onClick={onToggleLanguage} title={t('switchUiLanguage')} aria-label={t('switchUiLanguage')} className="rounded bg-[#8d5f35] px-1.5 py-0.5 text-[10px] text-white">{uiLocale === 'zh-CN' ? 'EN' : '中文'}</button>
      {window.aetherRuntime?.quickEntryShortcut && (
        <span>{ui('{shortcut} 快速输入', { shortcut: window.aetherRuntime.quickEntryShortcut })}</span>
      )}
      <span className="ml-auto">AetherChat Desktop · {ui('多模型 Agent 工作台')}</span>
    </footer>
  );
}
