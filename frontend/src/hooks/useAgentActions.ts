import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import { connectWS } from '../ws/client';
import type { AgentRun, AgentWorkspaceCandidate, GitRepository } from '../types';

export type UseAgentActionsParams = {
  sessionId: string;
  wsClient: MutableRefObject<ReturnType<typeof connectWS> | null>;
  setNotice: Dispatch<SetStateAction<string>>;
  setGitRepository: Dispatch<SetStateAction<GitRepository | null>>;
  setVerificationCommands: Dispatch<SetStateAction<string>>;
  setAgentRuns: Dispatch<SetStateAction<AgentRun[]>>;
};

export type UseAgentActionsResult = {
  selectGitRepository: () => Promise<void>;
  decideWorkspaceCandidate: (
    candidate: AgentWorkspaceCandidate,
    decision: 'apply' | 'reject',
  ) => Promise<void>;
  retryAgentRun: (run: AgentRun) => void;
  interruptAgentStep: (run: AgentRun, stepId: string) => void;
  updateAgentPlan: (
    run: AgentRun,
    goalSpec: {
      objective: string;
      success_criteria: string[];
      constraints: string[];
      deliverable: string;
    },
    planSteps: Array<{
      id: string;
      title: string;
      description: string;
      assigned_model?: string | null;
      role: string;
      dependencies: string[];
      success_criteria: string[];
    }>,
    changeSummary: string,
  ) => boolean;
  confirmAgentPlan: (run: AgentRun) => void;
  rejectAgentPlan: (run: AgentRun) => void;
};

export function useAgentActions({
  sessionId,
  wsClient,
  setNotice,
  setGitRepository,
  setVerificationCommands,
  setAgentRuns,
}: UseAgentActionsParams): UseAgentActionsResult {
  const selectGitRepository = async () => {
    if (!window.aetherWorkspace) {
      setNotice('选择本机 Git 仓库只在 AetherChat 桌面版中可用');
      return;
    }
    try {
      const repository = await window.aetherWorkspace.selectRepository(sessionId);
      if (!repository) return;
      setGitRepository(repository);
      setVerificationCommands(repository.verification_commands.join('\n'));
      setNotice(`已接入 Git 仓库 ${repository.display_name}；主仓库保持只读`);
    } catch (error) {
      setNotice(`Git 仓库接入失败：${String(error)}`);
    }
  };

  const decideWorkspaceCandidate = async (
    candidate: AgentWorkspaceCandidate,
    decision: 'apply' | 'reject',
  ) => {
    if (!window.aetherWorkspace) {
      setNotice('候选应用只在带原生确认的 AetherChat 桌面版中可用');
      return;
    }
    try {
      const updated = await window.aetherWorkspace.decideCandidate(
        candidate.id,
        sessionId,
        decision,
        {
          changedFiles: candidate.changed_files.length,
          verificationStatus: candidate.verification_status,
        },
      );
      if (!updated) return;
      setAgentRuns((items) => items.map((run) => ({
        ...run,
        workspace_candidates: run.workspace_candidates?.map((item) => (
          item.id === updated.id ? updated : item
        )),
      })));
      setNotice(decision === 'apply'
        ? '候选变更已应用到主仓库；未执行 commit 或 push'
        : '候选 worktree 已安全丢弃；主仓库未修改');
    } catch (error) {
      setNotice(`Git 候选处理失败：${String(error)}`);
    }
  };

  const retryAgentRun = (run: AgentRun) => {
    const sent = wsClient.current?.sendAction({ type: 'retry_agent_run', run_id: run.id });
    setNotice(sent ? '已提交失败步骤重试；已完成步骤不会重复执行' : '后端未连接，无法重试运行');
  };
  const interruptAgentStep = (run: AgentRun, stepId: string) => {
    const sent = wsClient.current?.sendAction({
      type: 'interrupt',
      run_id: run.id,
      step_id: stepId,
    });
    setNotice(sent ? '已提交计划步骤中断请求' : '后端未连接，无法中断计划步骤');
  };
  const updateAgentPlan = (
    run: AgentRun,
    goalSpec: {
      objective: string;
      success_criteria: string[];
      constraints: string[];
      deliverable: string;
    },
    planSteps: Array<{
      id: string;
      title: string;
      description: string;
      assigned_model?: string | null;
      role: string;
      dependencies: string[];
      success_criteria: string[];
    }>,
    changeSummary: string,
  ) => {
    const sent = wsClient.current?.sendAction({
      type: 'update_agent_plan',
      run_id: run.id,
      expected_revision: run.plan_revision,
      goal_spec: goalSpec,
      plan_steps: planSteps,
      change_summary: changeSummary,
    });
    setNotice(sent ? '正在保存计划草稿' : '后端未连接，无法保存计划');
    return Boolean(sent);
  };
  const confirmAgentPlan = (run: AgentRun) => {
    const sent = wsClient.current?.sendAction({
      type: 'confirm_agent_plan',
      run_id: run.id,
      expected_revision: run.plan_revision,
    });
    setNotice(sent ? '计划已确认，开始执行' : '后端未连接，无法确认计划');
  };
  const rejectAgentPlan = (run: AgentRun) => {
    const sent = wsClient.current?.sendAction({
      type: 'reject_agent_plan',
      run_id: run.id,
      expected_revision: run.plan_revision,
    });
    setNotice(sent ? '正在拒绝计划' : '后端未连接，无法拒绝计划');
  };

  return {
    selectGitRepository,
    decideWorkspaceCandidate,
    retryAgentRun,
    interruptAgentStep,
    updateAgentPlan,
    confirmAgentPlan,
    rejectAgentPlan,
  };
}
