import { useEffect, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import type { AgentActivityItem } from '../components/ModelWorkingStrip';
import { API_BASE } from '../components/shared';
import { connectWS } from '../ws/client';
import { useSessionStore } from '../stores/sessionStore';
import type { useI18n } from '../i18n';
import type { AgentRun, AgentWorkspaceCandidate, ApprovalRequest, WSMessage } from '../types';

type SessionStoreState = ReturnType<typeof useSessionStore.getState>;

export type UseSessionEventsParams = {
  sessionId: string;
  authToken: string;
  authReady: boolean;
  t: ReturnType<typeof useI18n>['t'];
  approvalActivity: MutableRefObject<Record<string, { model: string; toolName: string }>>;
  setNotice: Dispatch<SetStateAction<string>>;
  setAgentActivity: Dispatch<SetStateAction<Record<string, AgentActivityItem>>>;
  setApprovals: Dispatch<SetStateAction<ApprovalRequest[]>>;
  setAgentRuns: Dispatch<SetStateAction<AgentRun[]>>;
  setRunQueue: Dispatch<SetStateAction<{
    dispatchId: string;
    position: number | null;
  } | null>>;
  setConnection: SessionStoreState['setConnection'];
  addMessage: SessionStoreState['addMessage'];
  addDelta: SessionStoreState['addDelta'];
  addReasoningStep: SessionStoreState['addReasoningStep'];
  resetStream: SessionStoreState['resetStream'];
  finishStream: SessionStoreState['finishStream'];
  updateTask: SessionStoreState['updateTask'];
  upsertTool: SessionStoreState['upsertTool'];
  addToolRun: SessionStoreState['addToolRun'];
  setBudget: SessionStoreState['setBudget'];
  setActivePanel: SessionStoreState['setActivePanel'];
  loadTasks: () => void;
  loadMessages: () => void;
  loadTaskEvents: () => void;
  loadAgentRuns: () => void;
  loadGitRepositories: () => void;
  loadConversations: () => Promise<void>;
  loadArtifacts: () => void;
  loadMerges: () => void;
  loadTools: () => void;
  loadModels: () => void;
};

export type UseSessionEventsResult = {
  wsClient: MutableRefObject<ReturnType<typeof connectWS> | null>;
};

export function useSessionEvents({
  sessionId,
  authToken,
  authReady,
  t,
  approvalActivity,
  setNotice,
  setAgentActivity,
  setApprovals,
  setAgentRuns,
  setRunQueue,
  setConnection,
  addMessage,
  addDelta,
  addReasoningStep,
  resetStream,
  finishStream,
  updateTask,
  upsertTool,
  addToolRun,
  setBudget,
  setActivePanel,
  loadTasks,
  loadMessages,
  loadTaskEvents,
  loadAgentRuns,
  loadGitRepositories,
  loadConversations,
  loadArtifacts,
  loadMerges,
  loadTools,
  loadModels,
}: UseSessionEventsParams): UseSessionEventsResult {
  const wsClient = useRef<ReturnType<typeof connectWS> | null>(null);

  useEffect(() => {
    if (!authReady || !authToken) return;
    setConnection('connecting');
    let cancelled = false;
    const start = async () => {
      const resolveToken = async () => (
        authToken === 'intercept'
          ? String(await window.aetherRuntime?.getApiToken?.() || '')
          : authToken
      );
      if (cancelled || !(await resolveToken())) return;
      if (cancelled) return;
      wsClient.current = connectWS(sessionId, (event: WSMessage) => {
      if (event.type === 'stream_resync_required') {
        setAgentActivity({});
        loadTasks();
        loadMessages();
        loadTaskEvents();
        loadAgentRuns();
        loadGitRepositories();
        void loadConversations();
        void loadArtifacts();
        void loadMerges();
        loadTools();
        setNotice('事件游标已过期，已从本地数据库重新同步会话快照');
        return;
      }
      if (
        event.type === 'desktop_action_required'
        && event.action_id
        && event.desktop_action?.kind === 'open_external'
      ) {
        const actionId = event.action_id;
        const url = String(event.desktop_action.url || '');
        const browser = event.desktop_action.browser === 'firefox' ? 'firefox' : 'default';
        void (async () => {
          try {
            if (!window.aetherRuntime?.openExternal) {
              throw new Error('当前不是可执行外部导航的 AetherChat 桌面环境');
            }
            const receipt = await window.aetherRuntime.openExternal(url, { browser });
            const sent = wsClient.current?.sendAction({
              type: 'desktop_action_result',
              action_id: actionId,
              success: true,
              receipt,
            });
            if (!sent) throw new Error('桌面启动成功，但运行连接已断开，无法写入执行凭证');
            setNotice(browser === 'firefox'
              ? 'Firefox 已接受打开链接请求'
              : '系统浏览器已接受打开链接请求');
          } catch (error) {
            wsClient.current?.sendAction({
              type: 'desktop_action_result',
              action_id: actionId,
              success: false,
              error: String(error),
            });
            setNotice(`外部导航失败：${String(error)}`);
          }
        })();
      }
      if (event.type === 'provider_status' && event.model && event.phase) {
        setAgentActivity((items) => ({
          ...items,
          [event.model!]: {
            model: event.model!,
            phase: event.phase!,
            startedAt: items[event.model!]?.startedAt ?? Date.now(),
            updatedAt: Date.now(),
            toolName: items[event.model!]?.toolName,
            detail: event.detail,
            contextMessages: event.context_messages,
            contextTokens: event.context_tokens,
            toolCount: event.tool_count,
            toolSchemaTokens: event.tool_schema_tokens,
            roundIndex: event.round_index,
            firstTokenMs: event.first_token_ms,
          },
        }));
      }
      if (event.type === 'delta' && event.model && event.delta) {
        addDelta(event.model, event.delta);
        setAgentActivity((items) => ({
          ...items,
          [event.model!]: {
            ...items[event.model!],
            model: event.model!,
            phase: 'streaming',
            startedAt: items[event.model!]?.startedAt ?? Date.now(),
            updatedAt: Date.now(),
          },
        }));
      }
      if (event.type === 'reasoning_step' && event.model && event.step) {
        addReasoningStep(event.model, event.step, Boolean(event.reset));
      }
      if (event.type === 'stream_reset' && event.model) resetStream(event.model);
      if (event.type === 'message_complete' && event.message) {
        addMessage(event.message);
        if (event.message.author_model !== 'user') {
          finishStream(event.message.author_model);
          setAgentActivity((items) => {
            const next = { ...items };
            delete next[event.message!.author_model];
            return next;
          });
        }
        void loadConversations();
      }
      if (event.type === 'task_update' && event.task) updateTask(event.task);
      if (event.type === 'artifact_update') {
        void loadArtifacts();
      }
      if (event.type === 'tool_update' && event.tool) upsertTool(event.tool);
      if (event.type === 'tool_run' && event.run) {
        addToolRun(event.run);
        if (typeof event.run.arguments?.task_id === 'string') {
          loadTasks();
          loadTaskEvents();
        }
        if (event.run.status === 'success' && event.run.tool_name.includes('comfyui')) {
          void loadArtifacts();
        }
      }
      if (event.type === 'approval_required' && event.approval) {
        approvalActivity.current[event.approval.id] = {
          model: event.approval.model,
          toolName: event.approval.tool_name,
        };
        setAgentActivity((items) => ({
          ...items,
          [event.approval!.model]: {
            model: event.approval!.model,
            phase: 'waiting',
            toolName: event.approval!.tool_name,
            startedAt: items[event.approval!.model]?.startedAt ?? Date.now(),
            updatedAt: Date.now(),
          },
        }));
        setApprovals((items) => items.some((item) => item.id === event.approval?.id)
          ? items
          : [...items, event.approval as ApprovalRequest]);
      }
      if (event.type === 'approval_resolved' && event.approval_id && event.decision) {
        const resumed = approvalActivity.current[event.approval_id];
        if (resumed) {
          setAgentActivity((items) => ({
            ...items,
            [resumed.model]: {
              model: resumed.model,
              phase: 'tool',
              toolName: resumed.toolName,
              startedAt: items[resumed.model]?.startedAt ?? Date.now(),
              updatedAt: Date.now(),
            },
          }));
          delete approvalActivity.current[event.approval_id];
        }
        setApprovals((items) => items.map((item) => item.id === event.approval_id
          ? { ...item, decision: event.decision, feedback: event.feedback || item.feedback }
          : item));
      }
      if (event.type === 'tool_call' && event.model && event.tool_name) {
        setAgentActivity((items) => ({
          ...items,
          [event.model!]: {
            ...items[event.model!],
            model: event.model!,
            phase: 'tool',
            toolName: event.tool_name,
            startedAt: items[event.model!]?.startedAt ?? Date.now(),
            updatedAt: Date.now(),
          },
        }));
      }
      if (
        (event.type === 'tool_result' || event.type === 'tool_result_replayed')
        && event.model
      ) {
        setAgentActivity((items) => ({
          ...items,
          [event.model!]: {
            ...items[event.model!],
            model: event.model!,
            phase: 'requesting',
            detail: '工具执行完成，正在等待模型继续',
            startedAt: items[event.model!]?.startedAt ?? Date.now(),
            updatedAt: Date.now(),
          },
        }));
      }
      if (event.type === 'run_queued' && event.dispatch_id) {
        setRunQueue({
          dispatchId: event.dispatch_id,
          position: event.queue_position ?? null,
        });
        setNotice(event.queue_position
          ? `运行已排队，前方位置 ${event.queue_position}`
          : '运行已排队，正在等待调度');
      }
      if (event.type === 'run_dispatch_started' && event.dispatch_id) {
        setRunQueue((current) => current?.dispatchId === event.dispatch_id
          ? null
          : current);
        setNotice('排队结束，运行已获得执行槽');
      }
      if (event.type === 'run_cancelled' && event.dispatch_id) {
        setRunQueue((current) => current?.dispatchId === event.dispatch_id
          ? null
          : current);
        setNotice(event.detail || '排队运行已取消');
      }
      if (event.type === 'agent_run_update' && event.agent_run) {
        if (event.agent_run.status === 'running') setRunQueue(null);
        setAgentRuns((items) => items.some((item) => item.id === event.agent_run?.id)
          ? items.map((item) => item.id === event.agent_run?.id
            ? {
                ...event.agent_run as AgentRun,
                workspace_candidates: event.agent_run?.workspace_candidates
                  ?? item.workspace_candidates,
                workspace_candidate_count: event.agent_run?.workspace_candidate_count
                  ?? item.workspace_candidate_count,
              }
            : item)
          : [event.agent_run as AgentRun, ...items]);
      }
      if (event.type === 'plan_created' && event.run_id && event.goal && event.plan_steps) {
        setAgentRuns((items) => items.map((item) => item.id === event.run_id
          ? {
              ...item,
              goal: event.goal,
              plan_steps: event.plan_steps,
              plan_step_count: event.plan_steps?.length || 0,
              completed_plan_step_count: event.plan_steps?.filter((step) => step.status === 'completed').length || 0,
            }
          : item));
        setNotice(t('planCreated'));
      }
      if (event.type === 'plan_confirmation_required' && event.run_id) {
        if (event.agent_run) {
          const waitingRun = event.agent_run;
          setAgentRuns((items) => items.some((item) => item.id === waitingRun.id)
            ? items.map((item) => item.id === waitingRun.id ? waitingRun : item)
            : [waitingRun, ...items]);
        }
        setNotice('计划草稿已就绪，请审核、编辑或确认后再执行');
        setActivePanel('runs');
      }
      if (event.type === 'plan_updated' && event.run_id) {
        if (event.agent_run) {
          setAgentRuns((items) => items.map((item) => (
            item.id === event.run_id ? event.agent_run! : item
          )));
        } else {
          setAgentRuns((items) => items.map((item) => item.id === event.run_id
            ? {
                ...item,
                goal: event.goal || item.goal,
                plan_steps: event.plan_steps || item.plan_steps,
                plan_revision: event.revision || item.plan_revision,
              }
            : item));
        }
        setNotice(`计划草稿已保存为第 ${event.revision || '?'} 版`);
      }
      if (event.type === 'plan_replanned' && event.run_id && event.plan_steps) {
        setAgentRuns((items) => items.map((item) => item.id === event.run_id
          ? {
              ...item,
              plan_steps: event.plan_steps,
              plan_revision: event.revision || item.plan_revision,
              automatic_revision_count: event.automatic_revision_count
                ?? item.automatic_revision_count,
              plan_step_count: event.plan_steps?.length || 0,
              completed_plan_step_count: event.plan_steps?.filter(
                (step) => step.status === 'completed'
              ).length || 0,
            }
          : item));
        setNotice(`Verifier 已触发第 ${event.automatic_revision_count || 1} 次受控返工`);
      }
      if (event.type === 'plan_rejected' && event.agent_run) {
        setAgentRuns((items) => items.map((item) => (
          item.id === event.agent_run?.id ? event.agent_run! : item
        )));
        setNotice('计划已拒绝，未调用任何 Worker');
      }
      if (event.type === 'plan_step_update' && event.run_id && event.plan_step) {
        if (event.plan_step.assigned_model) {
          const model = event.plan_step.assigned_model;
          setAgentActivity((items) => {
            if (event.plan_step?.status === 'running') {
              return {
                ...items,
                [model]: {
                  model,
                  phase: 'running',
                  startedAt: items[model]?.startedAt ?? Date.now(),
                  updatedAt: Date.now(),
                },
              };
            }
            const next = { ...items };
            delete next[model];
            return next;
          });
        }
        setAgentRuns((items) => items.map((item) => {
          if (item.id !== event.run_id) return item;
          const current = item.plan_steps || [];
          const planSteps = current.some((step) => step.id === event.plan_step?.id)
            ? current.map((step) => step.id === event.plan_step?.id ? event.plan_step! : step)
            : [...current, event.plan_step!];
          return {
            ...item,
            plan_steps: planSteps,
            plan_step_count: planSteps.length,
            completed_plan_step_count: planSteps.filter((step) => step.status === 'completed').length,
          };
        }));
      }
      if (event.type === 'review_recorded' && event.run_id && event.review) {
        setAgentRuns((items) => items.map((item) => item.id === event.run_id
          ? {
              ...item,
              reviews: [...(item.reviews || []).filter((review) => review.id !== event.review?.id), event.review!],
              review_count: (item.reviews || []).some((review) => review.id === event.review?.id)
                ? item.review_count || item.reviews?.length || 0
                : (item.review_count || item.reviews?.length || 0) + 1,
            }
          : item));
      }
      if (event.type === 'workspace_verified' && event.run_id && event.workspace) {
        setAgentRuns((items) => items.map((item) => {
          if (item.id !== event.run_id) return item;
          const candidates = item.workspace_candidates || [];
          const next = candidates.some((candidate) => candidate.id === event.workspace?.id)
            ? candidates.map((candidate) => candidate.id === event.workspace?.id
              ? event.workspace as AgentWorkspaceCandidate
              : candidate)
            : [...candidates, event.workspace as AgentWorkspaceCandidate];
          return {
            ...item,
            workspace_candidates: next,
            workspace_candidate_count: next.length,
          };
        }));
      }
      if (event.type === 'plan_step_output' && event.model) finishStream(event.model);
      if (event.type === 'steer_ack') setNotice(t('steerQueued'));
      if (event.type === 'cost_update' && typeof event.budget_remaining === 'number') setBudget(event.budget_remaining);
      if (event.type === 'interrupted' && event.model) {
        finishStream(event.model);
        setAgentActivity((items) => {
          const next = { ...items };
          delete next[event.model!];
          return next;
        });
      }
      if (event.type === 'error' && (event as any).message) {
        const errorMessage = `${String((event as any).message)}${event.request_id ? `（诊断码：${event.request_id}）` : ''}`;
        if (event.model) {
          finishStream(event.model);
          setAgentActivity((items) => {
            const next = { ...items };
            delete next[event.model!];
            return next;
          });
        }
        setNotice(errorMessage);
      }
      if (event.type === 'ack' || ((event as any).type === 'system' && (event as any).content === 'Connected')) setConnection('online');
      if ((event as any).type === 'system' && (event as any).content === 'Disconnected') setConnection('offline');
      }, resolveToken, API_BASE);
      loadTasks();
      loadMessages();
      loadTaskEvents();
      loadAgentRuns();
      loadGitRepositories();
      loadArtifacts();
      loadMerges();
      loadTools();
      loadModels();
    };
    void start();
    return () => {
      cancelled = true;
      wsClient.current?.close();
    };
  }, [sessionId, authToken, authReady]);

  return { wsClient };
}
