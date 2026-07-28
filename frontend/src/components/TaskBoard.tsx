import { CheckCircle2, Circle, GitPullRequest, Play, UserPlus } from 'lucide-react';
import { useSessionStore } from '../stores/sessionStore';
import type { Task } from '../types';
import { useI18n } from '../i18n';

const columns: Array<{ id: Task['status']; label: string }> = [
  { id: 'backlog', label: '待办' },
  { id: 'claimed', label: '已认领' },
  { id: 'in_progress', label: '进行中' },
  { id: 'review', label: '评审' },
  { id: 'merged', label: '已合并' },
  { id: 'done', label: '已完成' },
];

const nextStatus: Partial<Record<Task['status'], Task['status']>> = {
  claimed: 'in_progress',
  in_progress: 'review',
  review: 'merged',
  merged: 'done',
};

const iconFor = (status: Task['status']) => {
  if (status === 'review') return <GitPullRequest size={14} />;
  if (status === 'merged' || status === 'done') return <CheckCircle2 size={14} />;
  return <Circle size={14} />;
};

interface TaskBoardProps {
  onPatchTask?: (task: Task, patch: Partial<Task>) => void;
}

export const TaskBoard = ({ onPatchTask }: TaskBoardProps) => {
  const { tasks } = useSessionStore();
  const { ui } = useI18n();

  return (
    <div className="space-y-4">
      {columns.map((column) => {
        const columnTasks = tasks.filter((task) => task.status === column.id);
        return (
          <section key={column.id}>
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-semibold text-[#7a7065]">
                {iconFor(column.id)}
                {ui(column.label)}
              </div>
              <span className="rounded bg-[#ebe3d8] px-1.5 py-0.5 text-xs text-[#7a7065]">{columnTasks.length}</span>
            </div>
            <div className="space-y-2">
              {columnTasks.map((task) => (
                <div key={task.id} className="rounded-lg border border-[#ded6ca] bg-[#fbfaf7] p-3 shadow-sm">
                  <div className="text-sm font-medium leading-5">{task.title}</div>
                  {task.description && <div className="mt-1 text-xs leading-5 text-[#7a7065]">{task.description}</div>}
                  {!!task.blocked_by?.length && (
                    <div className="mt-2 rounded bg-[#f5e7d1] px-2 py-1 text-[11px] text-[#7b571e]">
                      被 {task.blocked_by.length} 个前置任务阻塞
                    </div>
                  )}
                  <div className="mt-3 flex items-center justify-between text-xs text-[#7a7065]">
                    <span>{task.claimed_by ? `@${task.claimed_by}` : ui('未分配')}</span>
                    <span>{task.depends_on?.length ? `依赖 ${task.depends_on.length} · ` : ''}P{task.priority ?? 0}</span>
                  </div>
                  {onPatchTask && (
                    <div className="mt-3 flex gap-2">
                      {!task.claimed_by && (
                        <button
                          onClick={() => onPatchTask(task, { status: 'claimed', claimed_by: 'user' })}
                          disabled={Boolean(task.blocked_by?.length)}
                          className="flex items-center gap-1 rounded-md border border-[#ded6ca] px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-45"
                        >
                          <UserPlus size={13} />
                          {ui('认领')}
                        </button>
                      )}
                      {nextStatus[task.status] && (
                        <button
                          onClick={() => onPatchTask(task, { status: nextStatus[task.status] })}
                          disabled={Boolean(task.blocked_by?.length)}
                          className="flex items-center gap-1 rounded-md bg-[#2f2a24] px-2 py-1 text-xs text-white disabled:cursor-not-allowed disabled:opacity-45"
                        >
                          <Play size={13} />
                          {ui('推进')}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
};
