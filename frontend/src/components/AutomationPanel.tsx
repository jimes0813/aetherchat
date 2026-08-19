import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlarmClock,
  CalendarClock,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Pause,
  Play,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import { confirmAction } from './confirmDialog';
import { useI18n } from '../i18n';
import type {
  AutomationSchedule,
  BackgroundJob,
  BackgroundJobStatus,
} from '../types';

type JsonRequest = <T>(url: string, init?: RequestInit) => Promise<T>;

interface AutomationPanelProps {
  apiBase: string;
  sessionId: string;
  models: string[];
  request: JsonRequest;
  onOpenRuns: () => void;
}

interface SchedulerStatus {
  scheduler_running: boolean;
  active_workers: number;
  concurrency: number;
  enabled_schedules: number;
  queued_jobs: number;
  running_jobs: number;
  review_items: number;
}

const localDateTime = (value: Date) => {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
};

const statusTone: Record<BackgroundJobStatus, string> = {
  queued: 'bg-[#ece6dc] text-[#665d53]',
  running: 'bg-[#dbe7ee] text-[#315f78]',
  cancel_requested: 'bg-[#f5e7d1] text-[#7b571e]',
  waiting_review: 'bg-[#f5e7d1] text-[#7b571e]',
  completed: 'bg-[#dce9df] text-[#356345]',
  failed: 'bg-[#f2dfdb] text-[#8a3d32]',
  cancelled: 'bg-[#ebe7e1] text-[#746b61]',
};

export function AutomationPanel({
  apiBase,
  sessionId,
  models,
  request,
  onOpenRuns,
}: AutomationPanelProps) {
  const { ui } = useI18n();
  const [schedules, setSchedules] = useState<AutomationSchedule[]>([]);
  const [jobs, setJobs] = useState<BackgroundJob[]>([]);
  const [scheduler, setScheduler] = useState<SchedulerStatus | null>(null);
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [triggerType, setTriggerType] = useState<'once' | 'interval'>('interval');
  const [intervalMinutes, setIntervalMinutes] = useState(60);
  const [nextRunAt, setNextRunAt] = useState(
    localDateTime(new Date(Date.now() + 15 * 60_000)),
  );
  const [target, setTarget] = useState('all');
  const [maxBudget, setMaxBudget] = useState(1);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setBusy('load');
    try {
      const encodedSession = encodeURIComponent(sessionId);
      const [scheduleRows, jobRows, status] = await Promise.all([
        request<AutomationSchedule[]>(
          `${apiBase}/automations/schedules?session_id=${encodedSession}`,
        ),
        request<BackgroundJob[]>(
          `${apiBase}/automations/jobs?session_id=${encodedSession}`,
        ),
        request<SchedulerStatus>(`${apiBase}/automations/status`),
      ]);
      setSchedules(scheduleRows);
      setJobs(jobRows);
      setScheduler(status);
      if (!quiet) setMessage('');
    } catch (error) {
      if (!quiet) setMessage(`${ui('自动化加载失败：')}${String(error)}`);
    } finally {
      if (!quiet) setBusy('');
    }
  }, [apiBase, request, sessionId, ui]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 10_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const reviewJobs = useMemo(
    () => jobs.filter((job) => job.completed_at && !job.reviewed_at),
    [jobs],
  );

  const submit = async () => {
    if (!name.trim() || !prompt.trim() || !nextRunAt) {
      setMessage(ui('名称、指令和首次执行时间不能为空'));
      return;
    }
    setBusy('create');
    try {
      const schedule = await request<AutomationSchedule>(
        `${apiBase}/automations/schedules`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: sessionId,
            name: name.trim(),
            prompt: prompt.trim(),
            mode: 'project',
            targets: [target],
            trigger_type: triggerType,
            interval_minutes: triggerType === 'interval' ? intervalMinutes : null,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
            next_run_at: new Date(nextRunAt).toISOString(),
            enabled: true,
            max_budget_usd: maxBudget,
            permission_mode: 'read_only',
          }),
        },
      );
      setSchedules((items) => [schedule, ...items]);
      setName('');
      setPrompt('');
      setMessage(ui('定时任务已创建；权限快照固定为只读'));
      await load(true);
    } catch (error) {
      setMessage(`${ui('创建失败：')}${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const patchSchedule = async (
    schedule: AutomationSchedule,
    patch: Partial<AutomationSchedule>,
  ) => {
    setBusy(`schedule:${schedule.id}`);
    try {
      const updated = await request<AutomationSchedule>(
        `${apiBase}/automations/schedules/${schedule.id}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId, ...patch }),
        },
      );
      setSchedules((items) => items.map((item) => (
        item.id === updated.id ? updated : item
      )));
      setMessage(updated.enabled ? ui('定时任务已启用') : ui('定时任务已暂停'));
    } catch (error) {
      setMessage(`${ui('状态更新失败：')}${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const runNow = async (schedule: AutomationSchedule) => {
    setBusy(`schedule:${schedule.id}`);
    try {
      const job = await request<BackgroundJob>(
        `${apiBase}/automations/schedules/${schedule.id}/run`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId }),
        },
      );
      setJobs((items) => [job, ...items]);
      setMessage(ui('后台任务已排队，关闭窗口后仍会继续'));
      await load(true);
    } catch (error) {
      setMessage(`${ui('运行失败：')}${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const deleteSchedule = async (schedule: AutomationSchedule) => {
    if (!await confirmAction(ui('删除这个定时任务？已产生的运行记录会保留。'), { danger: true })) return;
    setBusy(`schedule:${schedule.id}`);
    try {
      await request(
        `${apiBase}/automations/schedules/${schedule.id}?session_id=${encodeURIComponent(sessionId)}`,
        { method: 'DELETE' },
      );
      setSchedules((items) => items.filter((item) => item.id !== schedule.id));
      setMessage(ui('定时任务已删除'));
    } catch (error) {
      setMessage(`${ui('删除失败：')}${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const cancelJob = async (job: BackgroundJob) => {
    setBusy(`job:${job.id}`);
    try {
      const updated = await request<BackgroundJob>(
        `${apiBase}/automations/jobs/${job.id}/cancel`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId }),
        },
      );
      setJobs((items) => items.map((item) => item.id === job.id ? updated : item));
      setMessage(ui('已请求取消后台任务'));
    } catch (error) {
      setMessage(`${ui('取消失败：')}${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const markReviewed = async (job: BackgroundJob) => {
    setBusy(`job:${job.id}`);
    try {
      const updated = await request<BackgroundJob>(
        `${apiBase}/automations/jobs/${job.id}/review`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId }),
        },
      );
      setJobs((items) => items.map((item) => item.id === job.id ? updated : item));
    } catch (error) {
      setMessage(`${ui('复核更新失败：')}${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const formatDate = (value?: string | null) => {
    if (!value) return ui('无后续执行');
    return new Intl.DateTimeFormat(undefined, {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(value));
  };

  const jobStatus = (status: BackgroundJobStatus) => ui(({
    queued: '排队中',
    running: '运行中',
    cancel_requested: '正在取消',
    waiting_review: '等待人工复核',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  } as const)[status]);

  return (
    <div className="space-y-5 p-4">
      <section className="rounded-xl border border-[#d9cdbf] bg-[#fffdf9] p-4 shadow-sm">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold">
              <AlarmClock size={17} />
              {ui('本机后台自动化')}
            </div>
            <p className="mt-1 text-xs leading-5 text-[#756a5f]">
              {ui('窗口关闭后由托盘中的本机服务继续执行；完全退出后，排队任务会在下次启动时恢复。')}
            </p>
          </div>
          <button
            type="button"
            aria-label={ui('刷新自动化状态')}
            onClick={() => void load()}
            disabled={busy === 'load'}
            className="rounded-md border border-[#d8ccbe] p-2 text-[#62584e] disabled:opacity-50"
          >
            <RefreshCw size={15} className={busy === 'load' ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[11px]">
          <div className="rounded-lg bg-[#f0ebe3] p-2">
            <div className="text-base font-semibold">{scheduler?.enabled_schedules ?? schedules.filter((item) => item.enabled).length}</div>
            <div className="text-[#756a5f]">{ui('已启用')}</div>
          </div>
          <div className="rounded-lg bg-[#e8eef1] p-2">
            <div className="text-base font-semibold">{scheduler?.running_jobs ?? 0}</div>
            <div className="text-[#756a5f]">{ui('后台运行')}</div>
          </div>
          <div className="rounded-lg bg-[#f5e7d1] p-2">
            <div className="text-base font-semibold">{reviewJobs.length}</div>
            <div className="text-[#756a5f]">{ui('待复核')}</div>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-[#d8e1d8] bg-[#edf4ee] px-3 py-2 text-xs text-[#3f6548]">
          <ShieldCheck size={15} />
          {ui('无人值守任务默认只读；高风险副作用不会弹出或继承临时会话授权。')}
        </div>
      </section>

      <section className="rounded-xl border border-[#d9cdbf] bg-[#fffdf9] p-4 shadow-sm">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <CalendarClock size={16} />
          {ui('创建定时任务')}
        </div>
        <div className="space-y-3">
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={ui('任务名称，例如：每日项目摘要')}
            maxLength={200}
            className="h-10 w-full rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none"
          />
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder={ui('后台执行指令')}
            maxLength={20_000}
            rows={4}
            className="w-full resize-y rounded-lg border border-[#ded6ca] bg-white p-3 text-sm outline-none"
          />
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-[#6d6258]">
              <span className="mb-1 block">{ui('触发方式')}</span>
              <select
                value={triggerType}
                onChange={(event) => setTriggerType(event.target.value as 'once' | 'interval')}
                className="h-10 w-full rounded-lg border border-[#ded6ca] bg-white px-2 text-sm"
              >
                <option value="interval">{ui('循环间隔')}</option>
                <option value="once">{ui('单次执行')}</option>
              </select>
            </label>
            <label className="text-xs text-[#6d6258]">
              <span className="mb-1 block">{ui('目标模型')}</span>
              <select
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                className="h-10 w-full rounded-lg border border-[#ded6ca] bg-white px-2 text-sm"
              >
                <option value="all">{ui('全部可用模型')}</option>
                {models.map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            </label>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-[#6d6258]">
              <span className="mb-1 block">{ui('首次执行')}</span>
              <input
                type="datetime-local"
                value={nextRunAt}
                onChange={(event) => setNextRunAt(event.target.value)}
                className="h-10 w-full rounded-lg border border-[#ded6ca] bg-white px-2 text-sm"
              />
            </label>
            {triggerType === 'interval' ? (
              <label className="text-xs text-[#6d6258]">
                <span className="mb-1 block">{ui('间隔分钟')}</span>
                <input
                  type="number"
                  min={5}
                  max={43_200}
                  value={intervalMinutes}
                  onChange={(event) => setIntervalMinutes(Number(event.target.value))}
                  className="h-10 w-full rounded-lg border border-[#ded6ca] bg-white px-2 text-sm"
                />
              </label>
            ) : (
              <label className="text-xs text-[#6d6258]">
                <span className="mb-1 block">{ui('预算上限（美元）')}</span>
                <input
                  type="number"
                  min={0.01}
                  max={50}
                  step={0.1}
                  value={maxBudget}
                  onChange={(event) => setMaxBudget(Number(event.target.value))}
                  className="h-10 w-full rounded-lg border border-[#ded6ca] bg-white px-2 text-sm"
                />
              </label>
            )}
          </div>
          {triggerType === 'interval' && (
            <label className="block text-xs text-[#6d6258]">
              <span className="mb-1 block">{ui('单次预算上限（美元）')}</span>
              <input
                type="number"
                min={0.01}
                max={50}
                step={0.1}
                value={maxBudget}
                onChange={(event) => setMaxBudget(Number(event.target.value))}
                className="h-10 w-full rounded-lg border border-[#ded6ca] bg-white px-2 text-sm"
              />
            </label>
          )}
          <button
            type="button"
            onClick={() => void submit()}
            disabled={busy === 'create'}
            className="flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-[#2f2a24] text-sm font-medium text-white disabled:opacity-50"
          >
            <AlarmClock size={15} />
            {busy === 'create' ? ui('创建中...') : ui('创建只读定时任务')}
          </button>
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold">{ui('定时任务')}</h3>
          <span className="text-xs text-[#7a7065]">{schedules.length}</span>
        </div>
        <div className="space-y-2">
          {schedules.map((schedule) => (
            <article key={schedule.id} className="rounded-xl border border-[#d9cdbf] bg-[#fffdf9] p-3 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{schedule.name}</div>
                  <div className="mt-1 line-clamp-2 text-xs leading-5 text-[#756a5f]">{schedule.prompt}</div>
                </div>
                <span className={`shrink-0 rounded-full px-2 py-1 text-[10px] ${schedule.enabled ? 'bg-[#dce9df] text-[#356345]' : 'bg-[#ebe7e1] text-[#746b61]'}`}>
                  {schedule.enabled ? ui('已启用') : ui('已暂停')}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[#786d62]">
                <span>{schedule.trigger_type === 'interval' ? ui('每 {count} 分钟', { count: schedule.interval_minutes ?? 0 }) : ui('单次执行')}</span>
                <span>{ui('下次：{value}', { value: formatDate(schedule.next_run_at) })}</span>
                <span>${Number(schedule.max_budget_usd).toFixed(2)}</span>
                <span>{ui('只读权限')}</span>
              </div>
              {schedule.last_error && <div className="mt-2 text-xs text-[#8a3d32]">{schedule.last_error}</div>}
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void runNow(schedule)}
                  disabled={busy === `schedule:${schedule.id}`}
                  className="flex items-center gap-1 rounded-md bg-[#2f2a24] px-2.5 py-1.5 text-xs text-white disabled:opacity-50"
                >
                  <Play size={13} /> {ui('立即运行')}
                </button>
                {schedule.next_run_at && (
                  <button
                    type="button"
                    onClick={() => void patchSchedule(schedule, { enabled: !schedule.enabled })}
                    disabled={busy === `schedule:${schedule.id}`}
                    className="flex items-center gap-1 rounded-md border border-[#d7cabc] px-2.5 py-1.5 text-xs disabled:opacity-50"
                  >
                    {schedule.enabled ? <Pause size={13} /> : <RotateCw size={13} />}
                    {schedule.enabled ? ui('暂停') : ui('启用')}
                  </button>
                )}
                <button
                  type="button"
                  aria-label={ui('删除定时任务 {name}', { name: schedule.name })}
                  onClick={() => void deleteSchedule(schedule)}
                  disabled={busy === `schedule:${schedule.id}`}
                  className="rounded-md border border-[#e2c9c3] p-1.5 text-[#8a3d32] disabled:opacity-50"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </article>
          ))}
          {!schedules.length && (
            <div className="rounded-xl border border-dashed border-[#d6cbbd] p-6 text-center text-xs text-[#7a7065]">
              {ui('当前会话还没有定时任务')}
            </div>
          )}
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <CircleAlert size={15} /> {ui('后台任务与复核收件箱')}
          </h3>
          <span className="rounded-full bg-[#f5e7d1] px-2 py-0.5 text-[10px] text-[#7b571e]">
            {ui('{count} 项待复核', { count: reviewJobs.length })}
          </span>
        </div>
        <div className="space-y-2">
          {jobs.slice(0, 50).map((job) => (
            <article key={job.id} className="rounded-xl border border-[#d9cdbf] bg-[#fffdf9] p-3 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{job.schedule_name || ui('已删除的定时任务')}</div>
                  <div className="mt-1 flex items-center gap-1 text-[11px] text-[#786d62]">
                    <Clock3 size={12} /> {formatDate(job.scheduled_for)} · {job.trigger_type === 'manual' ? ui('手动触发') : ui('计划触发')}
                  </div>
                </div>
                <span className={`shrink-0 rounded-full px-2 py-1 text-[10px] ${statusTone[job.status]}`}>
                  {jobStatus(job.status)}
                </span>
              </div>
              {(job.error || job.summary) && (
                <div className={`mt-2 line-clamp-4 rounded-lg px-2.5 py-2 text-xs leading-5 ${job.error ? 'bg-[#f8ece9] text-[#7f3d35]' : 'bg-[#f2eee8] text-[#62584e]'}`}>
                  {job.error || job.summary}
                </div>
              )}
              <div className="mt-2 text-[10px] text-[#83776b]">
                {ui('尝试 {attempt} 次 · 已用 ${spent} / ${limit}', {
                  attempt: job.attempt,
                  spent: Number(job.budget_spent_usd || 0).toFixed(4),
                  limit: Number(job.max_budget_usd).toFixed(2),
                })}
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {job.agent_run_id && (
                  <button type="button" onClick={onOpenRuns} className="flex items-center gap-1 rounded-md border border-[#d7cabc] px-2.5 py-1.5 text-xs">
                    <Play size={13} /> {ui('查看运行审计')}
                  </button>
                )}
                {['queued', 'running', 'cancel_requested'].includes(job.status) && (
                  <button
                    type="button"
                    onClick={() => void cancelJob(job)}
                    disabled={busy === `job:${job.id}` || job.status === 'cancel_requested'}
                    className="rounded-md border border-[#e2c9c3] px-2.5 py-1.5 text-xs text-[#8a3d32] disabled:opacity-50"
                  >
                    {ui('取消任务')}
                  </button>
                )}
                {job.completed_at && !job.reviewed_at && (
                  <button
                    type="button"
                    onClick={() => void markReviewed(job)}
                    disabled={busy === `job:${job.id}`}
                    className="flex items-center gap-1 rounded-md bg-[#496c51] px-2.5 py-1.5 text-xs text-white disabled:opacity-50"
                  >
                    <CheckCircle2 size={13} /> {ui('标记已复核')}
                  </button>
                )}
              </div>
            </article>
          ))}
          {!jobs.length && (
            <div className="rounded-xl border border-dashed border-[#d6cbbd] p-6 text-center text-xs text-[#7a7065]">
              {ui('尚无后台执行记录')}
            </div>
          )}
        </div>
      </section>

      {message && (
        <div role="status" className="sticky bottom-2 rounded-lg bg-[#2f2a24] px-3 py-2 text-xs text-white shadow-lg">
          {message}
        </div>
      )}
    </div>
  );
}
