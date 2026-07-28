// frontend/src/types/index.ts
export interface Message {
  id: string;
  session_id: string;
  author_model: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  created_at: string;
  tokens_in?: number;
  tokens_out?: number;
  cost_usd?: number;
  attachments?: KnowledgeAttachment[];
}

export interface KnowledgeAttachment {
  id: string;
  session_id?: string;
  name: string;
  extension?: string;
  content_type: string;
  size_bytes: number;
  status?: string;
  chunk_count: number;
  created_at?: string;
  preview_url?: string;
}

export interface ApprovalRequest {
  id: string;
  session_id: string;
  model: string;
  tool_name: string;
  risk: string;
  title: string;
  detail: string;
  arguments: Record<string, unknown>;
  allow_session: boolean;
  created_at: string;
  decision?: 'allow_once' | 'allow_session' | 'deny';
}

export interface SessionMemory {
  session_id: string;
  summary: string;
  project_note: string;
  decisions: string[];
  preferences: string[];
  open_items: string[];
  entities: SharedMemoryEntity[];
  message_count: number;
  updated_at?: string | null;
}

export interface SharedMemoryEntity {
  key: string;
  kind: string;
  value: string;
  source: string;
  metadata?: Record<string, string | number | boolean | null>;
  updated_at: string;
}

export type AgentRunStatus =
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'waiting_plan_confirmation'
  | 'completed'
  | 'failed'
  | 'interrupted'
  | 'recoverable';

export interface AgentStep {
  id: string;
  run_id: string;
  session_id: string;
  model: string;
  status: 'pending' | 'running' | 'waiting_approval' | 'completed' | 'failed' | 'interrupted';
  attempt: number;
  error?: string | null;
  updated_at: string;
}

export interface AgentGoal {
  run_id: string;
  session_id: string;
  objective: string;
  success_criteria: string[];
  constraints: string[];
  deliverable: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export type AgentPlanStepStatus =
  | 'pending'
  | 'ready'
  | 'running'
  | 'completed'
  | 'failed'
  | 'interrupted'
  | 'blocked'
  | 'skipped';

export interface AgentPlanStep {
  id: string;
  run_id: string;
  session_id: string;
  step_key: string;
  sequence: number;
  phase: number;
  kind: 'planner' | 'worker' | 'verifier' | 'synthesizer';
  title: string;
  description: string;
  assigned_model?: string | null;
  role: string;
  status: AgentPlanStepStatus;
  dependencies: string[];
  allow_failed_dependencies: boolean;
  input_contract: Record<string, unknown>;
  output_contract: Record<string, unknown>;
  success_criteria: string[];
  output?: Record<string, unknown> | null;
  output_sha256?: string | null;
  error?: string | null;
  attempt: number;
  updated_at: string;
}

export interface AgentReview {
  id: string;
  run_id: string;
  plan_step_id?: string | null;
  session_id: string;
  reviewer_model: string;
  verdict: 'pass' | 'needs_revision' | 'fail' | 'unknown';
  summary: string;
  evidence: unknown[];
  issues: unknown[];
  created_at: string;
}

export interface AgentPlanRevision {
  id: string;
  run_id: string;
  session_id: string;
  revision: number;
  source: 'planner' | 'user_edit' | 'verifier_replan';
  status: string;
  change_summary: string;
  goal: AgentGoal;
  steps: AgentPlanStep[];
  created_at: string;
  approved_at?: string | null;
}

export interface GitRepository {
  id: string;
  session_id: string;
  display_name: string;
  head_commit: string;
  head_short: string;
  branch: string;
  verification_commands: string[];
  status: 'ready' | string;
  updated_at?: string | null;
}

export interface WorkspaceVerification {
  command: string;
  exit_code: number;
  timed_out: boolean;
  network: string;
  output: string;
  output_sha256: string;
}

export interface AgentWorkspaceCandidate {
  id: string;
  run_id: string;
  plan_step_id: string;
  repository_id: string;
  attempt: number;
  base_commit: string;
  base_short: string;
  status: 'prepared' | 'verified' | 'failed' | 'applied' | 'rejected';
  changed_files: Array<{
    status: string;
    path: string;
    source_path?: string;
  }>;
  diff_sha256?: string | null;
  diff_bytes: number;
  verification_status: 'pending' | 'passed' | 'failed' | 'unverified' | 'no_changes';
  verification: WorkspaceVerification[];
  apply_status: 'pending' | 'applied' | 'rejected' | 'conflict';
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  verified_at?: string | null;
  applied_at?: string | null;
}

export interface AgentRun {
  id: string;
  session_id: string;
  status: AgentRunStatus;
  mode: string;
  input: {
    content?: string;
    targets?: string[];
    attachment_ids?: string[];
    execution_strategy?: 'direct' | 'planned';
    plan_confirmation_required?: boolean;
    max_revision_cycles?: number;
    repository_id?: string;
    repository_name?: string;
    repository_base_commit?: string;
    repository_branch?: string;
    verification_commands?: string[];
  };
  targets: string[];
  retry_count: number;
  plan_status?: 'none' | 'draft' | 'approved' | 'executing' | 'completed' | 'failed' | 'interrupted' | 'cancelled';
  plan_revision?: number;
  automatic_revision_count?: number;
  plan_approval_required?: boolean;
  plan_approved_at?: string | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  steps: AgentStep[];
  tool_call_count?: number;
  checkpoint_count?: number;
  steer_count?: number;
  plan_step_count?: number;
  completed_plan_step_count?: number;
  review_count?: number;
  goal?: AgentGoal | null;
  plan_steps?: AgentPlanStep[];
  reviews?: AgentReview[];
  plan_revisions?: AgentPlanRevision[];
  workspace_candidate_count?: number;
  workspace_candidates?: AgentWorkspaceCandidate[];
  events?: AgentEvent[];
  tool_calls?: AgentToolCall[];
}

export interface AgentEvent {
  id: string;
  run_id: string;
  step_id?: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AgentToolCall {
  id: string;
  run_id: string;
  model: string;
  tool_name: string;
  status: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface MCPToolInfo {
  name: string;
  title?: string | null;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface MCPConnection {
  id: string;
  name: string;
  transport: 'stdio' | 'streamable_http';
  command?: string | null;
  args: string[];
  cwd?: string | null;
  url?: string | null;
  auth_type: 'none' | 'bearer' | 'oauth_token' | 'oauth';
  has_token: boolean;
  enabled: boolean;
  allowed_tools: string[] | null;
  tools: MCPToolInfo[];
  server_info: Record<string, unknown>;
  status: 'disconnected' | 'ready' | 'error' | 'disabled' | 'authorizing';
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: string;
  session_id: string;
  title: string;
  description?: string;
  status: 'backlog' | 'claimed' | 'in_progress' | 'review' | 'merged' | 'done';
  claimed_by?: string;
  priority?: number;
  depends_on: string[];
  blocked_by?: string[];
  ready?: boolean;
}

export interface Artifact {
  id: string;
  session_id: string;
  path: string;
  kind: 'code' | 'doc' | 'prompt' | 'dataset' | 'binary';
  current_hash: string;
  content_hash?: string;
  mime_type?: string;
  current_version?: number;
  source_model?: string | null;
  status?: 'draft' | 'ready' | 'archived';
  parent_version?: number | null;
  size_bytes?: number;
  updated_at?: number;
}

export interface MergeProposal {
  id: string;
  session_id: string;
  artifact_path: string;
  proposer: string;
  from_hash?: string;
  to_hash?: string;
  rationale: string;
  status: 'open' | 'merged' | 'rejected';
  votes: Record<string, boolean>;
}

export interface WorkspaceSettings {
  mode: '日常讨论' | '圆桌评审' | '项目协作';
  mergeRule: '两票通过' | '主持人通过' | '人工确认';
  sandbox: 'Docker 严格沙箱' | '只读预览' | '关闭执行';
  language: '中文优先' | '中英双语';
  uiLocale: 'zh-CN' | 'en-US';
}

export interface ToolItem {
  id: string;
  name: string;
  description: string;
  category: string;
  owner: string;
  kind: 'template' | 'internal';
  input_schema: Record<string, unknown>;
  output_template: string;
  enabled: boolean;
  session_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ToolRun {
  id: string;
  session_id: string;
  tool_id?: string | null;
  tool_name: string;
  caller: string;
  arguments: Record<string, unknown>;
  output: string;
  result?: unknown;
  error?: string | null;
  status: 'success' | 'error';
  created_at: string;
}

export interface WSMessage {
  type: 'ack' | 'system' | 'delta' | 'stream_reset' | 'message_complete' | 'task_update' | 'artifact_update' | 'cost_update' | 'user_message' | 'tool_update' | 'tool_run' | 'tool_call' | 'tool_result' | 'tool_result_replayed' | 'approval_required' | 'approval_resolved' | 'agent_run_update' | 'plan_created' | 'plan_confirmation_required' | 'plan_updated' | 'plan_replanned' | 'plan_rejected' | 'plan_step_update' | 'plan_step_output' | 'review_recorded' | 'workspace_verified' | 'steer_ack' | 'steer_applied' | 'interrupted' | 'error';
  model?: string;
  delta?: string;
  seq?: number;
  ts?: string;
  message?: Message;
  task?: Task;
  budget_remaining?: number;
  content?: string;
  tool?: ToolItem;
  run?: ToolRun;
  approval?: ApprovalRequest;
  approval_id?: string;
  decision?: ApprovalRequest['decision'];
  agent_run?: AgentRun;
  goal?: AgentGoal;
  plan_steps?: AgentPlanStep[];
  plan_step?: AgentPlanStep;
  review?: AgentReview;
  workspace?: AgentWorkspaceCandidate;
  plan_step_id?: string;
  plan_step_key?: string;
  plan_step_kind?: AgentPlanStep['kind'];
  run_id?: string;
  revision?: number;
  automatic_revision_count?: number;
  trigger_review?: AgentReview;
  steer_id?: string;
  targets?: string[];
  tool_call_id?: string;
  tool_name?: string;
}

export interface Provider {
  name: string;
  role: string;
  color: string;
  status: 'idle' | 'thinking' | 'done' | 'error' | 'disabled';
  persona?: string;
  participation_policy?: string;
}

export interface ModelConfig {
  name: string;
  type: 'openai_compat' | 'anthropic' | 'xai' | 'local';
  model: string;
  base_url?: string;
  api_key_ref: string;
  has_api_key: boolean;
  max_tokens: number;
  participation_policy: 'always' | 'mentioned' | 'project_autonomous';
  persona?: string;
  token_limit: number;
  local_runtime?: 'external' | 'llama_cpp';
  model_path?: string;
  runtime_executable?: string;
  runtime_args?: string[];
  startup_timeout?: number;
  created_at: string;
  updated_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  summary: string;
  updatedAt: string;
  createdAt?: string;
  mode?: 'casual' | 'roundtable' | 'project';
  messageCount?: number;
  taskCount?: number;
  openTaskCount?: number;
}

export interface TaskEvent {
  id: string;
  task_id: string;
  session_id: string;
  action: 'created' | 'updated';
  task: Task;
  created_at: string;
}

export interface PluginAction {
  id: string;
  label: string;
  description: string;
  handler: string;
  input_schema: {
    type?: string;
    required?: string[];
    properties?: Record<string, { type?: string; title?: string; description?: string }>;
  };
  output_template?: string;
}

export interface PluginItem {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  category: string;
  icon: string;
  permissions: string[];
  actions: PluginAction[];
  builtin: boolean;
  enabled: boolean;
  config: Record<string, unknown>;
  status: 'ready' | 'error';
}

export interface PluginInvokeResult {
  plugin_id: string;
  plugin_name: string;
  action: string;
  status: 'success' | 'error';
  result: unknown;
  created_at: string;
}
