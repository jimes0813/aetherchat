import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Archive,
  ChevronRight,
  CircleDollarSign,
  FolderOpen,
  FolderPlus,
  ListTodo,
  MessageSquare,
  Search,
  Settings,
  SquarePen,
  Trash2,
  Users,
} from 'lucide-react';
import { DegradedInstallBanner } from './DegradedInstallBanner';
import { useDialogFocus } from './useDialogFocus';
import { API_BASE, formatDateTime, taskStatusLabel } from './shared';
import { fetchJson } from '../api/client';
import { useI18n } from '../i18n';
import { useSessionStore } from '../stores/sessionStore';
import { partitionConversations } from './conversationHygiene';
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

export const ACTIVITY_RAIL_ADVANCED_KEY = 'aetherchat.activity_rail.advanced.v1';

interface ActivityRailProps {
  settingsOpen?: boolean;
  onOpenChat?: () => void;
  onOpenSettings?: () => void;
  activePanel: ReturnType<typeof useSessionStore.getState>['activePanel'];
  setActivePanel: ReturnType<typeof useSessionStore.getState>['setActivePanel'];
}

export function ActivityRail({
  settingsOpen = false,
  onOpenChat,
  onOpenSettings,
  setActivePanel,
}: ActivityRailProps) {
  const { t } = useI18n();
  return (
    <aside className="flex min-h-0 flex-col items-center border-r border-[#d8cfc3] bg-[#2f2a24] py-2 text-[#d8cfc3]">
      <button
        type="button"
        title={t('chat')}
        aria-label={t('chat')}
        aria-pressed={!settingsOpen}
        onClick={() => {
          onOpenChat?.();
          setActivePanel(null);
        }}
        className={`flex h-8 w-8 items-center justify-center rounded-md transition ${!settingsOpen ? 'bg-[#8d5f35] text-white' : 'hover:bg-[#443b32]'}`}
      >
        <MessageSquare size={15} />
      </button>
      <button
        type="button"
        title={t('settings')}
        aria-label={t('settings')}
        aria-pressed={settingsOpen}
        onClick={() => {
          onOpenSettings?.();
          setActivePanel('settings');
        }}
        className={`mt-auto flex h-8 w-8 items-center justify-center rounded-md transition ${settingsOpen ? 'bg-[#8d5f35] text-white' : 'hover:bg-[#443b32]'}`}
      >
        <Settings size={15} />
      </button>
    </aside>
  );
}

interface WorkspaceSidebarProps {
  conversations: ReturnType<typeof useSessionStore.getState>['conversations'];
  currentSessionId: string;
  projects?: Array<{ id: string; name: string }>;
  activeProjectId?: string | null;
  /** Map of governance project id → display name. */
  projectNames?: Record<string, string>;
  onNewConversation: () => void;
  onNewProject?: (name: string) => void | Promise<void>;
  onSelectProject?: (id: string) => void;
  onOpenSearch: () => void;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
}

interface MainViewTabsProps {
  value: 'chat' | 'history' | 'tasks';
  onChange: (value: 'chat' | 'history' | 'tasks') => void;
}

export function MainViewTabs({ value, onChange }: MainViewTabsProps) {
  const { t } = useI18n();
  const tabs = [
    { id: 'chat' as const, label: t('currentChat'), icon: MessageSquare },
    { id: 'history' as const, label: t('chatHistory'), icon: Archive },
    { id: 'tasks' as const, label: t('taskRecords'), icon: ListTodo },
  ];
  return (
    <nav role="tablist" aria-label={t('sessionViews')} className="flex items-center gap-0.5">
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
            className={`inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs ${selected ? 'bg-[#efe6db] font-medium text-[#5d3b20]' : 'text-[#766b60] hover:bg-[#f3ece3] hover:text-[#2f2a24]'}`}
          >
            <Icon size={13} />
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
  projectNames?: Record<string, string>;
  onContinue: (id: string) => void;
  onDelete: (id: string) => void;
}

export function ConversationHistory({
  conversations,
  currentSessionId,
  projectNames,
  onContinue,
  onDelete,
}: ConversationHistoryProps) {
  const { t, ui } = useI18n();
  const [query, setQuery] = useState('');
  const [showDiagnostic, setShowDiagnostic] = useState(false);
  const { visible, diagnostic } = partitionConversations(conversations);
  const listed = showDiagnostic ? conversations : visible;
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
            <h2 className="text-base font-semibold">{t('chatHistory')}</h2>
            <p className="mt-1 text-xs text-[#7a7065]">{t('historyRestoreHint')}</p>
          </div>
          <span className="text-xs text-[#8b8176]">{t('conversationCount', { count: listed.length })}</span>
        </div>
        <label data-aether-search-shell className="mb-4 flex h-10 items-center gap-2 rounded-lg border border-[#d8cfc3] bg-white px-3">
          <Search size={15} className="text-[#776b60]" />
          <span className="sr-only">{t('searchAllChats')}</span>
          <input
            data-aether-search
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('searchAllChatsPlaceholder')}
            className="min-w-0 flex-1 bg-transparent text-sm outline-none"
          />
          {searching && <span className="text-[11px] text-[#776b60]">{t('searching')}</span>}
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
              <div className="py-10 text-center text-sm text-[#8b8176]">{t('noMatchingChats')}</div>
            )}
          </div>
        )}
        {!query.trim() && (
        <div className="divide-y divide-[#e3dbd0] border-y border-[#e3dbd0]">
          {listed.map((conversation) => (
            <div key={conversation.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-stretch">
              <button
                type="button"
                onClick={() => onContinue(conversation.id)}
                className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-4 px-2 py-4 text-left hover:bg-[#f5efe7]"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">{ui(conversation.title)}</span>
                    {conversation.id === currentSessionId && <span className="shrink-0 rounded bg-[#e7d8c8] px-1.5 py-0.5 text-[10px] text-[#6d4828]">{t('current')}</span>}
                    {conversation.projectId && (
                      <span className="shrink-0 rounded bg-[#ebe4da] px-1.5 py-0.5 text-[10px] text-[#5d4a3a]">
                        {ui(projectNames?.[conversation.projectId] || conversation.projectId)}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-[#6f655b]">{conversation.summary}</p>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[#93887d]">
                    <span>{t('messageCount', { count: conversation.messageCount ?? 0 })}</span>
                    <span>{t('taskCount', { count: conversation.taskCount ?? 0 })}</span>
                    <span>{t('openTaskCount', { count: conversation.openTaskCount ?? 0 })}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2 self-center text-xs text-[#8b8176]">
                  <span>{conversation.updatedAt}</span>
                  <ChevronRight size={15} />
                </div>
              </button>
              <button
                type="button"
                aria-label={t('deleteConversationNamed', { title: conversation.title })}
                title={t('deleteConversation')}
                onClick={() => onDelete(conversation.id)}
                className="my-3 mr-2 flex w-9 items-center justify-center rounded-md text-[#815b43] hover:bg-[#f0dfd5] hover:text-[#8f2f25]"
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
          {!listed.length && <div className="py-12 text-center text-sm text-[#8b8176]">{t('noChatHistory')}</div>}
        </div>
        )}
        {!query.trim() && diagnostic.length > 0 && (
          <button
            type="button"
            onClick={() => setShowDiagnostic((current) => !current)}
            className="mt-3 text-xs text-[#8b8176] hover:text-[#5d3b20]"
          >
            {showDiagnostic
              ? t('hideDiagnostic')
              : t('showDiagnostic', { count: diagnostic.length })}
          </button>
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
  const { t, ui } = useI18n();
  return (
    <div className="min-h-0 flex-1 overflow-auto bg-[#fbfaf7] px-5 py-4">
      <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section>
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-base font-semibold">{t('currentTasks')}</h2>
            <span className="text-xs text-[#8b8176]">{t('itemCount', { count: tasks.length })}</span>
          </div>
          <div className="divide-y divide-[#e3dbd0] border-y border-[#e3dbd0]">
            {tasks.map((task) => (
              <div key={task.id} className="px-2 py-3">
                <div className="flex items-start justify-between gap-3">
                  <span className="text-sm font-medium leading-5">{task.title}</span>
                  <span className="shrink-0 rounded bg-[#ece4d9] px-2 py-0.5 text-[11px] text-[#665a4e]">{ui(taskStatusLabel[task.status])}</span>
                </div>
                {task.description && <p className="mt-1 text-xs leading-5 text-[#74695e]">{task.description}</p>}
                {task.claimed_by && <div className="mt-2 text-[11px] text-[#93887d]">{t('ownerNamed', { name: task.claimed_by })}</div>}
                {!!task.depends_on?.length && (
                  <div className="mt-1 text-[11px] text-[#93887d]">
                    {t('dependencyCount', { count: task.depends_on.length })}
                    {task.blocked_by?.length
                      ? t('stillBlocked', { count: task.blocked_by.length })
                      : t('dependenciesMet')}
                  </div>
                )}
              </div>
            ))}
            {!tasks.length && <div className="py-10 text-center text-sm text-[#8b8176]">{t('noTasksInChat')}</div>}
          </div>
        </section>
        <section>
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-base font-semibold">{t('changeLog')}</h2>
            <span className="text-xs text-[#8b8176]">{t('eventCount', { count: events.length })}</span>
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
                  {event.action === 'created' ? t('taskCreated') : t('taskUpdated')} · {ui(taskStatusLabel[event.task.status])}
                  {event.task.claimed_by ? ` · ${event.task.claimed_by}` : ''}
                </p>
              </div>
            ))}
            {!events.length && <div className="py-10 text-center text-sm text-[#8b8176]">{t('noTaskChanges')}</div>}
          </div>
        </section>
      </div>
    </div>
  );
}

export function WorkspaceSidebar({
  conversations,
  currentSessionId,
  projects = [],
  activeProjectId,
  projectNames,
  onNewConversation,
  onNewProject,
  onSelectProject,
  onOpenSearch,
  onSelectConversation,
  onDeleteConversation,
}: WorkspaceSidebarProps) {
  const { t, ui } = useI18n();
  const [projectDraft, setProjectDraft] = useState<string | null>(null);
  const [showDiagnostic, setShowDiagnostic] = useState(false);
  const { visible, diagnostic } = partitionConversations(conversations);
  const listed = showDiagnostic ? conversations : visible;
  const submitProject = () => {
    const name = projectDraft?.trim();
    if (!name || !onNewProject) {
      setProjectDraft(null);
      return;
    }
    void onNewProject(name);
    setProjectDraft(null);
  };
  const rowClass = (active: boolean) =>
    `flex h-8 w-full items-center gap-1 rounded-md px-2 text-left text-[13px] ${
      active ? 'bg-[#e4d5c3] text-[#3d2c1c]' : 'text-[#4a4037] hover:bg-[#e8ddd0]'
    }`;

  return (
    <aside className="flex min-h-0 flex-col bg-[#f3ece3]" data-testid="workspace-sidebar">
      <div className="flex flex-col gap-0.5 px-2 pt-2">
        <button
          type="button"
          onClick={onNewConversation}
          aria-label={t('newConversation')}
          className={rowClass(false)}
        >
          <SquarePen size={14} className="shrink-0 text-[#6d5744]" />
          {t('newChat')}
        </button>
        <button
          type="button"
          onClick={() => setProjectDraft('')}
          className={rowClass(false)}
        >
          <FolderPlus size={14} className="shrink-0 text-[#6d5744]" />
          {t('newProject')}
        </button>
        <button type="button" onClick={onOpenSearch} className={rowClass(false)}>
          <Search size={14} className="shrink-0 text-[#6d5744]" />
          {t('search')}
        </button>
      </div>

      {projectDraft !== null && (
        <form
          className="px-2 pt-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            submitProject();
          }}
        >
          <input
            autoFocus
            value={projectDraft}
            onChange={(event) => setProjectDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault();
                setProjectDraft(null);
              }
            }}
            onBlur={() => {
              if (!projectDraft?.trim()) setProjectDraft(null);
            }}
            placeholder={t('projectName')}
            aria-label={t('projectName')}
            className="h-8 w-full rounded-md border border-[#d8cfc3] bg-white px-2 text-[13px] outline-none"
          />
        </form>
      )}

      {projects.length > 0 && (
        <div className="mt-3 min-h-0">
          <div className="px-3 pb-1 text-[11px] text-[#8b8176]">{t('projects')}</div>
          <div className="px-1.5">
            {projects.map((project) => (
              <button
                key={project.id}
                type="button"
                aria-current={project.id === activeProjectId ? 'true' : undefined}
                onClick={() => onSelectProject?.(project.id)}
                className={rowClass(project.id === activeProjectId)}
              >
                <span className="truncate">{ui(project.name)}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mt-3 px-3 pb-1 text-[11px] text-[#8b8176]">{t('chatsSection')}</div>
      <div className="min-h-0 flex-1 overflow-auto px-1.5 pb-2">
        {listed.map((conversation) => (
          <div key={conversation.id} className="group relative">
            <button
              type="button"
              onClick={() => onSelectConversation(conversation.id)}
              aria-current={conversation.id === currentSessionId ? 'page' : undefined}
              title={ui(conversation.title)}
              className={`${rowClass(conversation.id === currentSessionId)} pr-7`}
            >
              <span className="min-w-0 flex-1 truncate">{ui(conversation.title)}</span>
            </button>
            <button
              type="button"
              aria-label={t('deleteConversationNamed', { title: ui(conversation.title) })}
              title={t('deleteConversation')}
              onClick={() => onDeleteConversation(conversation.id)}
              className="absolute right-1 top-1 flex h-6 w-6 items-center justify-center rounded text-[#7b5a42] opacity-0 transition hover:text-[#962f25] focus:opacity-100 group-hover:opacity-100"
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        {diagnostic.length > 0 && (
          <button
            type="button"
            onClick={() => setShowDiagnostic((current) => !current)}
            className="mt-2 w-full rounded-md px-2 py-1 text-left text-[11px] text-[#8b8176] hover:bg-[#e8ddd0]"
          >
            {showDiagnostic
              ? t('hideDiagnostic')
              : t('showDiagnostic', { count: diagnostic.length })}
          </button>
        )}
      </div>
    </aside>
  );
}

interface CommandHeaderProps {
  connection: ReturnType<typeof useSessionStore.getState>['connection'];
  providerCount: number;
  activeProviderCount: number;
  budgetRemaining: number;
  onOpenSearch: () => void;
}

export function CommandHeader({
  connection,
  providerCount,
  activeProviderCount,
  budgetRemaining,
  onOpenSearch,
}: CommandHeaderProps) {
  const { t } = useI18n();
  return (
    <header className="flex h-9 shrink-0 items-center gap-2 border-b border-[#e7dfd3] bg-[#fbfaf7] px-3">
      <div className="min-w-0">
        <div className="truncate text-xs font-semibold">
          <span>AetherChat</span>
          <span className="hidden sm:inline"> · {t('collaborationWorkspace')}</span>
        </div>
      </div>
      <button onClick={onOpenSearch} className="mx-auto hidden h-7 min-w-[12rem] max-w-md flex-1 items-center gap-1.5 rounded-md border border-[#d8cfc3] bg-white px-2 text-left text-[11px] text-[#8b8176] lg:flex">
        <Search size={12} />
        {t('searchShortcutHint')}
      </button>
      <div className="ml-auto flex items-center gap-1.5">
        <div className="hidden items-center gap-1 rounded-md border border-[#e1d9cd] bg-white px-1.5 py-0.5 text-[11px] text-[#6d6258] sm:flex">
          <Users size={12} />
          {t('modelsConfigured', { active: activeProviderCount, total: providerCount })}
        </div>
        <div className="flex items-center gap-1.5 rounded-md border border-[#e1d9cd] bg-white px-1.5 py-0.5 text-[11px] text-[#7a7065]">
          <CircleDollarSign size={12} className="text-[#8d5f35]" />
          <span>{t('sessionAllowance')}</span>
          <span className="tabular-nums">${budgetRemaining.toFixed(2)}</span>
        </div>
        <div className="flex items-center gap-1 rounded-md bg-[#2f2a24] px-1.5 py-0.5 text-[11px] font-medium text-white">
          <Activity size={12} />
          {connection === 'online' ? t('connected') : connection === 'connecting' ? t('connecting') : t('offline')}
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
  const { t } = useI18n();
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
        aria-label={t('commandPaletteLabel')}
        className="w-full max-w-2xl overflow-hidden rounded-xl border border-[#d8cfc3] bg-[#fbfaf7] shadow-2xl"
        onKeyDown={dialogFocus.onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div data-aether-search-shell className="flex items-center gap-3 border-b border-[#e1d9cd] px-4">
          <Search size={17} className="text-[#7a7065]" />
          <input
            data-aether-search
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
            placeholder={t('commandPlaceholder')}
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
            <div className="px-3 py-8 text-center text-sm text-[#8b8176]">{t('noMatches')}</div>
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
    <>
      <DegradedInstallBanner />
    <footer className="flex h-7 items-center gap-4 border-t border-[#cfc4b7] bg-[#2f2a24] px-3 text-[11px] text-[#e7dfd3]">
      <span className="flex items-center gap-1"><FolderOpen size={12} /> {t('localWorkspace')}</span>
      <span>{t('sandbox', { value: ui(sandbox) })}</span>
      <span>WS {connection === 'online' ? t('connected') : connection === 'connecting' ? t('connecting') : t('offline')}</span>
      {runQueuePosition !== undefined && (
        <button
          type="button"
          onClick={onCancelQueuedRun}
          aria-label={t('cancelQueuedRun')}
          title={t('cancelQueuedRun')}
          className="rounded bg-[#6f5843] px-1.5 py-0.5 text-[10px] text-white"
        >
          {runQueuePosition ? t('runQueuedCancelN', { value: runQueuePosition }) : t('runQueuedCancel')}
        </button>
      )}
      <span>{t('budget', { value: budgetRemaining.toFixed(2) })}</span>
      {window.aetherRuntime?.chromiumSandbox === 'off' && (
        <span
          className="rounded bg-[#8a4036] px-1.5 py-0.5 text-[10px] text-white"
          title={t('unpackedNoSetuid')}
        >
          {t('degradedNoSandbox')}
          {window.aetherRuntime?.installChannel ? ` · ${window.aetherRuntime.installChannel}` : ''}
        </span>
      )}
      <button onClick={onRefreshToken} title={t('refreshWsAuth')} className="rounded bg-[#8d5f35] px-1.5 py-0.5 text-[10px] text-white">{ui(tokenStatus)}</button>
      <button onClick={onToggleLanguage} title={t('switchUiLanguage')} aria-label={t('switchUiLanguage')} className="rounded bg-[#8d5f35] px-1.5 py-0.5 text-[10px] text-white">{uiLocale === 'zh-CN' ? 'EN' : t('chinese')}</button>
      {window.aetherRuntime?.quickEntryShortcut && (
        <span>{t('quickEntry', { shortcut: window.aetherRuntime.quickEntryShortcut })}</span>
      )}
      <span className="ml-auto">AetherChat Desktop · {t('multiModelWorkbench')}</span>
    </footer>
    </>
  );
}
