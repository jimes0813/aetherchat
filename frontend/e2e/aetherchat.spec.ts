import { expect, test, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const sessionId = '11111111-1111-4111-8111-111111111111';

const modelFixtures = [
  {
    name: 'deepseek',
    type: 'openai_compat',
    model: 'deepseek-chat',
    base_url: 'https://api.deepseek.com',
    api_key_ref: 'DEEPSEEK_API_KEY',
    has_api_key: true,
    max_tokens: 8192,
    participation_policy: 'always',
    persona: '编码 / 推理',
    token_limit: 128000,
    created_at: '2026-01-01T00:00:00.000Z',
    updated_at: '2026-01-01T00:00:00.000Z',
  },
  {
    name: 'GLM',
    type: 'openai_compat',
    model: 'glm-5',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    api_key_ref: 'MODEL_KEY_GLM',
    has_api_key: true,
    max_tokens: 8192,
    participation_policy: 'mentioned',
    persona: '中文评审',
    token_limit: 128000,
    created_at: '2026-01-01T00:00:00.000Z',
    updated_at: '2026-01-01T00:00:00.000Z',
  },
  {
    name: 'claude',
    type: 'anthropic',
    model: 'claude-3-5-sonnet',
    base_url: '',
    api_key_ref: 'ANTHROPIC_API_KEY',
    has_api_key: false,
    max_tokens: 8192,
    participation_policy: 'always',
    persona: '架构评审',
    token_limit: 128000,
    created_at: '2026-01-01T00:00:00.000Z',
    updated_at: '2026-01-01T00:00:00.000Z',
  },
];

const taskFixtures = [
  { id: 'task-backlog', session_id: sessionId, title: '待办任务', description: '等待处理', status: 'backlog', priority: 0 },
  { id: 'task-claimed', session_id: sessionId, title: '已认领任务', description: '已有负责人', status: 'claimed', claimed_by: 'deepseek', priority: 1 },
  { id: 'task-progress', session_id: sessionId, title: '进行中任务', description: '正在实现', status: 'in_progress', claimed_by: 'deepseek', priority: 2 },
  { id: 'task-review', session_id: sessionId, title: '评审任务', description: '等待评审', status: 'review', claimed_by: 'GLM', priority: 1 },
  { id: 'task-merged', session_id: sessionId, title: '已合并任务', description: '等待归档', status: 'merged', claimed_by: 'deepseek', priority: 1 },
  { id: 'task-done', session_id: sessionId, title: '已完成任务', description: '工作完成', status: 'done', claimed_by: 'GLM', priority: 1 },
];

const sessionFixtures = [
  {
    id: sessionId,
    title: 'AetherChat 持久化工作区',
    summary: '继续数据库与历史会话功能',
    mode: 'project',
    created_at: '2026-07-12T08:00:00.000Z',
    updated_at: '2026-07-13T08:30:00.000Z',
    last_message_at: '2026-07-13T08:30:00.000Z',
    message_count: 2,
    task_count: taskFixtures.length,
    open_task_count: 4,
  },
  {
    id: '22222222-2222-4222-8222-222222222222',
    title: '本地模型协作排查',
    summary: 'Qwen 与 ComfyUI 调用记录',
    mode: 'casual',
    created_at: '2026-07-11T07:00:00.000Z',
    updated_at: '2026-07-11T09:00:00.000Z',
    last_message_at: '2026-07-11T09:00:00.000Z',
    message_count: 8,
    task_count: 1,
    open_task_count: 0,
  },
];

const messageFixtures = [
  { id: 'message-internal-system', session_id: sessionId, author_model: 'system', role: 'system', content: 'INTERNAL_MODEL_HISTORY_REQUEST', created_at: '2026-07-13T08:28:58.000Z' },
  { id: 'message-internal-tool', session_id: sessionId, author_model: 'tool', role: 'tool', content: 'INTERNAL_TOOL_RESULT_HISTORY', created_at: '2026-07-13T08:28:59.000Z' },
  { id: 'message-user', session_id: sessionId, author_model: 'user', role: 'user', content: '继续数据库任务', created_at: '2026-07-13T08:29:00.000Z' },
  {
    id: 'message-qwen',
    session_id: sessionId,
    author_model: 'qwen',
    role: 'assistant',
    content: '已恢复上次上下文。',
    generated_files: [{
      path: 'generated/comfyui/cover.png',
      name: 'cover.png',
      mime_type: 'image/png',
      size_bytes: 2048,
      kind: 'generated_media',
      source: 'comfyui',
    }],
    reasoning_summary: [
      {
        phase: 'analysis',
        detail: '已整理 6 条上下文，并筛选 5 个与请求相关的可用工具。',
        ts: '2026-07-13T08:29:58.000Z',
      },
      {
        phase: 'result',
        detail: '工具 plugin__comfyui__generate_image 执行成功，正在依据真实结果继续处理。',
        ts: '2026-07-13T08:29:59.000Z',
      },
      {
        phase: 'final',
        detail: '已核对模型输出与工具结果，正在整理最终答复。',
        ts: '2026-07-13T08:30:00.000Z',
      },
    ],
    created_at: '2026-07-13T08:30:00.000Z',
  },
];

const taskEventFixtures = [
  { id: 'event-1', task_id: 'task-progress', session_id: sessionId, action: 'updated', task: taskFixtures[2], created_at: '2026-07-13T08:31:00.000Z' },
];

const artifactFixtures = [
  {
    id: 'artifact-readme',
    session_id: sessionId,
    path: 'README.md',
    kind: 'doc',
    current_hash: '1234567890abcdef',
    content_hash: '1234567890abcdef',
    mime_type: 'text/markdown',
    current_version: 2,
    status: 'ready',
    source_model: 'deepseek',
    parent_version: 1,
    size_bytes: 48,
    updated_at: 1,
  },
  {
    id: 'artifact-history-only',
    session_id: sessionId,
    path: 'notes/deleted.md',
    kind: 'doc',
    current_hash: 'feedface12345678',
    content_hash: 'feedface12345678',
    mime_type: 'text/markdown',
    current_version: 1,
    status: 'ready',
    source_model: 'deepseek',
    parent_version: null,
    size_bytes: 34,
    updated_at: '2026-07-15T09:00:00.000Z',
    availability: 'history_only',
    recoverable: true,
  },
];

const artifactVersions = [
  {
    id: 'version-2', version: 2, hash: '1234567890abcdef', short_hash: '1234567',
    message: '完善说明', author: 'deepseek', source_model: 'deepseek', parent_version: 1,
    created_at: '2026-07-15T10:00:00.000Z', content: '# AetherChat\n\n安全工件预览。', content_encoding: 'utf-8',
  },
  {
    id: 'version-1', version: 1, hash: 'abcdef1234567890', short_hash: 'abcdef1',
    message: '初始版本', author: 'system', created_at: '2026-07-14T10:00:00.000Z',
    content: '# AetherChat\n', content_encoding: 'utf-8',
  },
];

const deletedArtifactVersions = [{
  id: 'deleted-version-1', version: 1, hash: 'feedface12345678', short_hash: 'feedfac',
  message: '删除前快照', author: 'deepseek', source_model: 'deepseek', parent_version: null,
  created_at: '2026-07-15T09:00:00.000Z', content: '# Deleted\n\n可恢复的历史内容。', content_encoding: 'utf-8',
}];

const pluginFixtures = [
  {
    id: 'qa-diagnostics',
    name: '应用 QA 诊断',
    version: '1.0.0',
    description: '执行只读工作区检查',
    author: 'AetherChat',
    category: 'Developer',
    icon: 'ShieldCheck',
    permissions: ['workspace_read', 'task_read'],
    actions: [{
      id: 'workspace_health',
      label: '工作区健康检查',
      description: '不调用模型或外部服务',
      handler: 'workspace_health',
      input_schema: { type: 'object', properties: {} },
    }],
    builtin: true,
    enabled: true,
    config: {},
    status: 'ready',
  },
  {
    id: 'browser-links',
    name: '浏览器链接',
    version: '1.0.0',
    description: '打开经过验证的 HTTP(S) 链接',
    author: 'AetherChat',
    category: 'Browser',
    icon: 'Globe2',
    permissions: ['external_navigation'],
    actions: [{
      id: 'open_url',
      label: '打开链接',
      description: '只允许 HTTP(S)',
      handler: 'open_url',
      input_schema: { type: 'object', required: ['url'], properties: { url: { type: 'string', title: '链接地址' } } },
    }],
    builtin: true,
    enabled: true,
    config: {},
    status: 'ready',
  },
];

async function installMockWebSocket(page: Page, autoOpen = true) {
  await page.addInitScript(({ mockSessionId, shouldOpen }) => {
    const actions: unknown[] = [];
    let activeSocket: { onmessage: ((event: MessageEvent) => void) | null } | null = null;
    const testWindow = window as typeof window & {
      __aetherWsActions?: unknown[];
      __emitAetherWs?: (payload: unknown) => void;
      __aetherWsReady?: () => boolean;
    };
    testWindow.__aetherWsActions = actions;
    testWindow.__emitAetherWs = (payload: unknown) => {
      activeSocket?.onmessage?.(new MessageEvent('message', {
        data: JSON.stringify(payload),
      }));
    };
    testWindow.__aetherWsReady = () => Boolean(activeSocket?.onmessage);

    class MockWebSocket {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSING = 2;
      static readonly CLOSED = 3;

      readonly url: string;
      readyState = MockWebSocket.CONNECTING;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;

      constructor(url: string | URL) {
        this.url = String(url);
        activeSocket = this;
        if (!shouldOpen) return;
        queueMicrotask(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.(new Event('open'));
        });
      }

      send(data: string) {
        const action = JSON.parse(String(data));
        actions.push(action);
        if (action.type === 'update_agent_plan') {
          queueMicrotask(() => {
            this.onmessage?.(new MessageEvent('message', {
              data: JSON.stringify({
                type: 'plan_updated',
                run_id: action.run_id,
                revision: Number(action.expected_revision || 0) + 1,
              }),
            }));
          });
          return;
        }
        if (action.type !== 'post_message') return;

        queueMicrotask(() => {
          this.onmessage?.(new MessageEvent('message', {
            data: JSON.stringify({ type: 'ack' }),
          }));
          if (String(action.content).includes('运行转向')) {
            this.onmessage?.(new MessageEvent('message', {
              data: JSON.stringify({
                type: 'steer_ack',
                run_id: '99999999-9999-4999-8999-999999999999',
                steer_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                targets: action.targets || [],
              }),
            }));
            this.onmessage?.(new MessageEvent('message', {
              data: JSON.stringify({
                type: 'message_complete',
                message: {
                  id: `message-${actions.length}`,
                  session_id: mockSessionId,
                  author_model: 'user',
                  role: 'user',
                  content: action.content,
                  created_at: new Date().toISOString(),
                },
              }),
            }));
            return;
          }
          if (String(action.content).includes('需要授权')) {
            this.onmessage?.(new MessageEvent('message', {
              data: JSON.stringify({
                type: 'approval_required',
                approval: {
                  id: '66666666-6666-4666-8666-666666666666',
                  session_id: mockSessionId,
                  model: 'deepseek',
                  tool_name: 'run_command',
                  risk: 'command_execution',
                  title: '允许模型运行沙箱命令？',
                  detail: 'npm test',
                  arguments: { cmd: 'npm test' },
                  allow_session: true,
                  created_at: new Date().toISOString(),
                },
              }),
            }));
            return;
          }
          this.onmessage?.(new MessageEvent('message', {
            data: JSON.stringify({
              type: 'message_complete',
              message: {
                id: `message-${actions.length}`,
                session_id: mockSessionId,
                author_model: 'user',
                role: 'user',
                content: action.content,
                created_at: new Date().toISOString(),
              },
            }),
          }));
        });
      }

      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.(new CloseEvent('close'));
      }
    }

    Object.defineProperty(window, 'WebSocket', {
      configurable: true,
      value: MockWebSocket,
    });
  }, { mockSessionId: sessionId, shouldOpen: autoOpen });
}

async function installMockDesktopBridge(page: Page) {
  await page.addInitScript(() => {
    const actions: Array<{ type: string; payload?: unknown }> = [];
    let launchIntentHandler: ((intent: unknown) => void) | null = null;
    const testWindow = window as typeof window & {
      __aetherDesktopActions?: typeof actions;
      __emitAetherLaunchIntent?: (intent: unknown) => void;
    };
    testWindow.__aetherDesktopActions = actions;
    testWindow.__emitAetherLaunchIntent = (intent) => launchIntentHandler?.(intent);
    Object.defineProperty(window, 'aetherRuntime', {
      configurable: true,
      value: {
        platform: 'linux',
        backendStatus: 'ready',
        onLaunchIntent: (callback: (intent: unknown) => void) => {
          launchIntentHandler = callback;
          return () => { launchIntentHandler = null; };
        },
        openExternal: async (
          url: string,
          options: { browser?: 'default' | 'firefox' } = {},
        ) => {
          const browser = options.browser || 'default';
          const receipt = {
            success: true as const,
            kind: 'external_navigation' as const,
            url: new URL(url).toString(),
            browser,
            launcher: browser === 'firefox' ? 'firefox' as const : 'system_default' as const,
            verification: 'launcher_accepted' as const,
            accepted_at: '2026-08-01T10:05:52.000Z',
          };
          actions.push({ type: 'open-external', payload: receipt });
          return receipt;
        },
        openArtifact: async (
          selectedSessionId: string,
          path: string,
          action: 'open' | 'reveal',
        ) => {
          actions.push({
            type: `artifact-${action}`,
            payload: { selectedSessionId, path },
          });
          return { opened: true, action, displayName: 'cover.png' };
        },
      },
    });
    Object.defineProperty(window, 'aetherDesktop', {
      configurable: true,
      value: {
        status: async () => ({
          available: true,
          file_read: true,
          file_write: true,
          document_formats: ['txt', 'md', 'json', 'csv', 'tsv', 'docx', 'xlsx', 'pptx', 'pdf'],
          mouse: true,
          model_computer_use: true,
          keyboard: true,
          computer_protocol: 'computer_20251124',
          computer_actions: [
            'screenshot', 'mouse_move', 'left_click', 'right_click', 'middle_click',
            'double_click', 'triple_click', 'left_click_drag', 'left_mouse_down',
            'left_mouse_up', 'scroll', 'type', 'key', 'hold_key', 'wait', 'zoom',
            'cursor_position',
          ],
          permission_mode: 'manual',
          screenshot: 'electron',
        }),
        selectDocument: async () => ({
          handle: 'mock-handle',
          displayName: 'mock-notes.md',
          document: {
            name: 'mock-notes.md',
            format: 'md',
            size_bytes: 12,
            modified_at: '2026-01-01T00:00:00Z',
            editor_mode: 'text',
            data: { text: 'mock before' },
          },
        }),
        saveDocument: async (handle: string, data: unknown) => {
          actions.push({ type: 'save', payload: { handle, data } });
          return {
            saved: true,
            displayName: 'mock-notes.md',
            sizeBytes: 10,
            savedAt: '2026-01-01T00:00:00Z',
            backupCreated: true,
            backupDisplayName: 'mock-notes.aetherchat-backup.md',
          };
        },
        saveDocumentAs: async (handle: string, data: unknown) => {
          actions.push({ type: 'save-as', payload: { handle, data } });
          return {
            saved: true,
            handle: 'mock-handle-2',
            displayName: 'mock-notes-edited.md',
            sizeBytes: 10,
            savedAt: '2026-01-01T00:00:00Z',
            backupCreated: true,
            backupDisplayName: 'mock-notes-edited.aetherchat-backup.md',
          };
        },
        captureScreen: async () => ({
          dataUrl: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII=',
          width: 1920,
          height: 1080,
          capturedAt: '2026-01-01T00:00:00Z',
        }),
        saveScreenshot: async () => ({
          saved: true,
          displayName: 'AetherChat-test.png',
          locationLabel: '图片/Screenshots',
          sizeBytes: 68,
        }),
        mouse: async (action: 'get' | 'move' | 'click', options: { x?: number; y?: number } = {}) => {
          actions.push({ type: `mouse-${action}`, payload: options });
          return {
            action,
            position: { x: options.x ?? 50, y: options.y ?? 60 },
            virtual_desktop: { left: 0, top: 0, width: 1920, height: 1080 },
          };
        },
        computerAction: async (action: string, options: Record<string, unknown> = {}) => {
          actions.push({ type: `computer-${action}`, payload: options });
          const coordinate = Array.isArray(options.coordinate) ? options.coordinate : [50, 60];
          const cursor = { x: Number(coordinate[0]), y: Number(coordinate[1]) };
          const screenshot = {
            image_data_url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII=',
            width: 1280,
            height: 720,
            display_width_px: 1280,
            display_height_px: 720,
            native_width: 1920,
            native_height: 1080,
            origin_x: 0,
            origin_y: 0,
            scale_x: 1.5,
            scale_y: 1.5,
          };
          return {
            protocol: 'computer_20251124' as const,
            action_id: `mock-${action}-${Date.now()}`,
            action,
            status: 'success' as const,
            started_at: '2026-01-01T00:00:00Z',
            completed_at: '2026-01-01T00:00:00Z',
            duration_ms: 42,
            result: action === 'cursor_position' ? { position: cursor } : {},
            cursor_before: { x: 50, y: 60 },
            cursor_after: cursor,
            verification: ['screenshot', 'zoom', 'cursor_position', 'wait'].includes(action)
              ? 'observation' as const
              : 'post_action_screenshot' as const,
            screenshot: action === 'cursor_position' || action === 'wait' ? undefined : screenshot,
          };
        },
        setComputerPermission: async (mode: string) => {
          actions.push({ type: 'computer-permission', payload: mode });
          return { permissionMode: mode };
        },
        cancelComputer: async () => {
          actions.push({ type: 'computer-cancel' });
          return { cancelled: true, permissionMode: 'manual' as const };
        },
        computerHistory: async () => ({
          events: [],
          latest_sequence: 0,
          active_action_id: null,
          latest_receipt: null,
        }),
        onComputerEvent: () => () => undefined,
      },
    });
    Object.defineProperty(window, 'aetherWorkspace', {
      configurable: true,
      value: {
        selectRepository: async (selectedSessionId: string) => {
          actions.push({ type: 'select-repository', payload: selectedSessionId });
          return {
            id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            session_id: selectedSessionId,
            display_name: 'mock-git-repository',
            head_commit: '0123456789abcdef0123456789abcdef01234567',
            head_short: '0123456789ab',
            branch: 'main',
            verification_commands: ['python -m pytest -q'],
            status: 'ready',
            updated_at: '2026-07-19T00:00:00Z',
          };
        },
        decideCandidate: async (
          workspaceId: string,
          selectedSessionId: string,
          decision: 'apply' | 'reject',
          summary: unknown,
        ) => {
          actions.push({
            type: `workspace-${decision}`,
            payload: { workspaceId, selectedSessionId, summary },
          });
          return {
            id: workspaceId,
            run_id: '77777777-7777-4777-8777-777777777777',
            plan_step_id: 'candidate-plan-2',
            repository_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            attempt: 1,
            base_commit: '0123456789abcdef0123456789abcdef01234567',
            base_short: '0123456789ab',
            status: decision === 'apply' ? 'applied' : 'rejected',
            changed_files: [{ status: ' M', path: 'src/app.ts' }],
            diff_sha256: 'abcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd',
            diff_bytes: 512,
            verification_status: 'passed',
            verification: [{
              command: 'npm test',
              exit_code: 0,
              timed_out: false,
              network: 'none',
              output: '28 passed',
              output_sha256: 'test-output-hash',
            }],
            apply_status: decision === 'apply' ? 'applied' : 'rejected',
            error: null,
            updated_at: new Date().toISOString(),
            applied_at: decision === 'apply' ? new Date().toISOString() : null,
          };
        },
      },
    });
  });
}

async function installMockApi(page: Page) {
  const tasks = taskFixtures.map((task) => ({ ...task }));
  const plugins = pluginFixtures.map((plugin) => ({ ...plugin }));
  const sessions = sessionFixtures.map((session) => ({ ...session }));
  const knowledgeAttachments: Array<Record<string, unknown>> = [];
  let historyArtifactRestored = false;
  const governanceProjects = [{
    id: 'default',
    tenant_id: 'local',
    name: '默认项目',
    slug: 'default',
    description: '本机默认项目',
    status: 'active',
  }];
  const sessionMemory = {
    session_id: sessionId,
    summary: '最近对话摘要：\n- 用户: 继续数据库任务',
    project_note: '',
    decisions: ['决定使用 SQLite'],
    preferences: ['优先使用中文'],
    open_items: ['下一步补充恢复测试'],
    entities: [{
      key: 'asset:comfyui:cover',
      kind: 'generated_asset',
      value: 'generated/comfyui/cover.metadata.json',
      source: 'model:qwen:generate_image',
      metadata: { seed: 42 },
      updated_at: '2026-07-15T00:00:00Z',
    }],
    message_count: 2,
    updated_at: '2026-07-15T00:00:00Z',
  };
  const agentRuns = [{
    id: '88888888-8888-4888-8888-888888888888',
    session_id: sessionId,
    status: 'recoverable',
    mode: 'project',
    input: {
      content: '继续实现可恢复 Agent',
      targets: ['deepseek'],
      execution_strategy: 'planned',
      effective_execution_strategy: 'planned',
      execution_strategy_source: 'explicit',
    },
    effective_execution_strategy: 'planned',
    execution_strategy_source: 'explicit',
    targets: ['deepseek'],
    retry_count: 0,
    tool_call_count: 2,
    effect_count: 1,
    verified_effect_count: 0,
    failed_effect_count: 0,
    uncertain_effect_count: 0,
    effect_verification_status: 'unverified',
    checkpoint_count: 1,
    steer_count: 1,
    plan_step_count: 5,
    completed_plan_step_count: 2,
    review_count: 1,
    goal: {
      run_id: '88888888-8888-4888-8888-888888888888',
      session_id: sessionId,
      objective: '完成可恢复 Agent 计划',
      success_criteria: ['已完成步骤不重复执行'],
      constraints: ['副作用必须授权'],
      deliverable: '一份经过核验的实现结果',
      status: 'active',
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:01:00Z',
    },
    plan_steps: [
      {
        id: 'plan-1', step_key: 'planner', sequence: 0, phase: 0, kind: 'planner',
        title: '形成目标与执行计划', description: '', assigned_model: null,
        role: 'Deterministic Planner', status: 'completed', dependencies: [],
        allow_failed_dependencies: false, input_contract: {}, output_contract: {},
        success_criteria: [], output: null, attempt: 0, updated_at: '2026-07-15T00:00:00Z',
      },
      {
        id: 'plan-2', step_key: 'worker:1:deepseek', sequence: 1, phase: 1, kind: 'worker',
        title: 'deepseek 独立执行', description: '', assigned_model: 'deepseek',
        role: 'Coder', status: 'interrupted', dependencies: ['planner'],
        allow_failed_dependencies: false, input_contract: {}, output_contract: {},
        success_criteria: [], output: null, attempt: 1, updated_at: '2026-07-15T00:01:00Z',
      },
      {
        id: 'plan-3', step_key: 'worker:2:GLM', sequence: 2, phase: 1, kind: 'worker',
        title: 'GLM 独立执行', description: '', assigned_model: 'GLM',
        role: 'Reviewer', status: 'completed', dependencies: ['planner'],
        allow_failed_dependencies: false, input_contract: {}, output_contract: {},
        success_criteria: [], output: null, attempt: 1, updated_at: '2026-07-15T00:01:00Z',
      },
      {
        id: 'plan-4', step_key: 'verifier', sequence: 3, phase: 2, kind: 'verifier',
        title: '独立核验候选结果', description: '', assigned_model: 'GLM',
        role: 'Verifier / Critic', status: 'pending',
        dependencies: ['worker:1:deepseek', 'worker:2:GLM'],
        allow_failed_dependencies: true, input_contract: {}, output_contract: {},
        success_criteria: [], output: null, attempt: 0, updated_at: '2026-07-15T00:01:00Z',
      },
      {
        id: 'plan-5', step_key: 'synthesizer', sequence: 4, phase: 3, kind: 'synthesizer',
        title: '生成单一最终交付', description: '', assigned_model: 'deepseek',
        role: 'Lead Synthesizer', status: 'pending',
        dependencies: ['worker:1:deepseek', 'worker:2:GLM', 'verifier'],
        allow_failed_dependencies: true, input_contract: {}, output_contract: {},
        success_criteria: [], output: null, attempt: 0, updated_at: '2026-07-15T00:01:00Z',
      },
    ],
    reviews: [{
      id: 'review-1',
      run_id: '88888888-8888-4888-8888-888888888888',
      plan_step_id: 'plan-4',
      session_id: sessionId,
      reviewer_model: 'GLM',
      verdict: 'needs_revision',
      summary: '需要恢复 deepseek Worker 后再完成综合。',
      evidence: [],
      issues: [],
      created_at: '2026-07-15T00:01:00Z',
    }],
    error: '应用退出前运行尚未完成',
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:01:00Z',
    steps: [{
      id: 'step-1', run_id: '88888888-8888-4888-8888-888888888888', session_id: sessionId,
      model: 'deepseek', status: 'interrupted', attempt: 1,
      error: '应用重启导致步骤中断', updated_at: '2026-07-15T00:01:00Z',
    }],
  }];
  const draftRun: any = JSON.parse(JSON.stringify(agentRuns[0]));
  draftRun.id = '99999999-9999-4999-8999-999999999999';
  draftRun.status = 'waiting_plan_confirmation';
  draftRun.input = {
    content: '先确认部署计划',
    targets: ['deepseek', 'GLM'],
    execution_strategy: 'planned',
    plan_confirmation_required: true,
    max_revision_cycles: 1,
  };
  draftRun.targets = ['deepseek', 'GLM'];
  draftRun.retry_count = 0;
  draftRun.tool_call_count = 0;
  draftRun.checkpoint_count = 0;
  draftRun.steer_count = 0;
  draftRun.completed_plan_step_count = 1;
  draftRun.review_count = 0;
  draftRun.plan_status = 'draft';
  draftRun.plan_revision = 1;
  draftRun.automatic_revision_count = 0;
  draftRun.plan_approval_required = true;
  draftRun.plan_approved_at = null;
  draftRun.goal = {
    ...draftRun.goal,
    run_id: draftRun.id,
    objective: '审核部署范围和回滚策略',
    success_criteria: ['包含回滚条件', '确认后才执行'],
    constraints: ['确认前 Worker 只读且不启动'],
    deliverable: '一份经过确认的部署计划',
  };
  draftRun.plan_steps = draftRun.plan_steps.map((step: any) => ({
    ...step,
    id: `draft-${step.id}`,
    run_id: draftRun.id,
    status: step.kind === 'planner' ? 'completed' : 'pending',
    error: null,
    attempt: 0,
    success_criteria: step.success_criteria.length
      ? step.success_criteria
      : ['满足当前步骤职责'],
  }));
  draftRun.reviews = [];
  draftRun.error = null;
  agentRuns.push(draftRun);
  const candidateRun: any = JSON.parse(JSON.stringify(agentRuns[0]));
  candidateRun.id = '77777777-7777-4777-8777-777777777777';
  candidateRun.status = 'completed';
  candidateRun.input = {
    content: '已完成代码候选',
    targets: ['deepseek'],
    execution_strategy: 'planned',
    repository_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    repository_name: 'mock-git-repository',
    repository_branch: 'main',
    repository_base_commit: '0123456789abcdef0123456789abcdef01234567',
    verification_commands: ['npm test'],
  };
  candidateRun.targets = ['deepseek'];
  candidateRun.plan_status = 'completed';
  candidateRun.plan_steps = candidateRun.plan_steps.map((step: any) => ({
    ...step,
    id: `candidate-${step.id}`,
    run_id: candidateRun.id,
    status: 'completed',
    error: null,
  }));
  candidateRun.completed_plan_step_count = candidateRun.plan_steps.length;
  candidateRun.reviews = [{
    ...candidateRun.reviews[0],
    id: 'candidate-review',
    run_id: candidateRun.id,
    verdict: 'pass',
    summary: '确定性 diff 与 npm test 均通过。',
  }];
  candidateRun.error = null;
  candidateRun.workspace_candidate_count = 1;
  candidateRun.workspace_candidates = [{
    id: '66666666-6666-4666-8666-666666666667',
    run_id: candidateRun.id,
    plan_step_id: 'candidate-plan-2',
    repository_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    attempt: 1,
    base_commit: '0123456789abcdef0123456789abcdef01234567',
    base_short: '0123456789ab',
    status: 'verified',
    changed_files: [{ status: ' M', path: 'src/app.ts' }],
    diff_sha256: 'abcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd',
    diff_bytes: 512,
    verification_status: 'passed',
    verification: [{
      command: 'npm test',
      exit_code: 0,
      timed_out: false,
      network: 'none',
      output: '28 passed',
      output_sha256: 'test-output-hash',
    }],
    apply_status: 'pending',
    error: null,
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:01:00Z',
    verified_at: '2026-07-19T00:01:00Z',
    applied_at: null,
  }];
  agentRuns.push(candidateRun);
  const mcpConnections: Array<Record<string, any>> = [{
    id: 'local-files', name: '本地项目文件', transport: 'stdio', command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem', '/workspace'], cwd: null, url: null,
    auth_type: 'none', has_token: false, enabled: true, allowed_tools: null,
    tools: [{ name: 'read_file', title: '读取文件', description: '读取获准目录中的文件', input_schema: { type: 'object' } }],
    server_info: { name: 'filesystem' }, status: 'ready', error: null,
    created_at: '2026-07-15T00:00:00Z', updated_at: '2026-07-15T00:00:00Z',
  }];
  const automationSchedules: Array<Record<string, any>> = [{
    id: '99999999-9999-4999-8999-999999999999',
    session_id: sessionId,
    name: '每日项目摘要',
    prompt: '只读检查当前项目并生成摘要。',
    mode: 'project',
    targets: ['all'],
    trigger_type: 'interval',
    interval_minutes: 1440,
    timezone: 'Asia/Chongqing',
    next_run_at: '2026-08-02T01:00:00Z',
    enabled: true,
    max_budget_usd: 1,
    permission_snapshot: {
      policy_version: 1,
      mode: 'read_only',
      allowed_tools: [],
      captured_at: '2026-08-01T00:00:00Z',
    },
    last_run_at: '2026-08-01T01:00:00Z',
    last_status: 'completed',
    last_error: null,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T01:00:00Z',
  }];
  const backgroundJobs: Array<Record<string, any>> = [{
    id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    schedule_id: automationSchedules[0].id,
    schedule_name: automationSchedules[0].name,
    session_id: sessionId,
    agent_run_id: agentRuns[0].id,
    trigger_type: 'schedule',
    status: 'completed',
    action: { content: automationSchedules[0].prompt, targets: ['all'], mode: 'project' },
    permission_snapshot: automationSchedules[0].permission_snapshot,
    max_budget_usd: 1,
    budget_spent_usd: 0.0312,
    scheduled_for: '2026-08-01T01:00:00Z',
    attempt: 1,
    summary: '已完成只读项目检查，未执行任何副作用。',
    error: null,
    reviewed_at: null,
    created_at: '2026-08-01T01:00:00Z',
    updated_at: '2026-08-01T01:01:00Z',
    started_at: '2026-08-01T01:00:00Z',
    completed_at: '2026-08-01T01:01:00Z',
  }];

  const json = (route: Route, body: unknown, status = 200) => route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  });

  await page.route(/^http:\/\/(localhost|127\.0\.0\.1):8000\/.*/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const { pathname } = url;

    if (pathname === '/health') return json(route, { status: 'ok', service: 'aetherchat', redis: false });
    if (pathname === '/health/ready') return json(route, {
      status: 'ready',
      ready: true,
      service: 'aetherchat',
      checks: { database: { status: 'ready', schema_version: 4, expected_schema_version: 4 } },
      optional_dependencies: { redis: { status: 'unavailable', required: false } },
      request_id: 'e2e-readiness-request',
    });
    if (pathname === '/platform/status' && request.method() === 'GET') return json(route, {
      phase: 'phase_3',
      phase_label: '本机平台治理',
      default_tenant_id: 'local',
      default_project_id: 'default',
      tenant_count: 1,
      project_count: governanceProjects.length,
      principal_count: 1,
      policy: {
        environment: 'local',
        enforce_tool_allowlist: false,
        enforce_model_allowlist: false,
        require_approval_for_high_risk: true,
        dlp_redact_secrets: true,
        audit_enabled: true,
      },
      runtime_gate: { enabled: true, env: 'local' },
      audit_retention: { days: 30, max_events: 10000 },
      capabilities: { sso: false, scim: false },
      notes: [],
    });
    if (pathname === '/platform/projects' && request.method() === 'GET') {
      return json(route, governanceProjects);
    }
    if (pathname === '/platform/projects' && request.method() === 'POST') {
      const payload = request.postDataJSON();
      const project = {
        id: `project-${governanceProjects.length + 1}`,
        tenant_id: payload.tenant_id || 'local',
        name: payload.name,
        slug: String(payload.name || 'project').toLowerCase().replace(/\s+/g, '-'),
        description: payload.description || null,
        status: 'active',
      };
      governanceProjects.push(project);
      return json(route, project, 201);
    }
    if (pathname === '/platform/principals' && request.method() === 'GET') return json(route, [{
      id: 'local-owner',
      display_name: '本机所有者',
      role: 'owner',
      status: 'active',
      project_id: 'default',
    }]);
    if (pathname === '/platform/audit' && request.method() === 'GET') return json(route, []);
    if (pathname === '/platform/authorize' && request.method() === 'POST') {
      return json(route, { allowed: true, message: 'local policy allowed' });
    }
    if (/^\/platform\/tenants\/[^/]+\/policy$/.test(pathname) && request.method() === 'PATCH') {
      return json(route, request.postDataJSON());
    }
    if (pathname === '/platform/audit/export' && request.method() === 'GET') {
      return json(route, {
        format: url.searchParams.get('format') || 'json',
        count: 0,
        exported_at: '2026-08-13T00:00:00Z',
        events: [],
        body: '',
      });
    }
    if (pathname === '/platform/audit/prune' && request.method() === 'POST') {
      return json(route, { removed: 0, kept: 0, retention_days: 30, max_events: 10000 });
    }
    if (pathname === '/sessions/' && request.method() === 'GET') return json(route, sessions);
    if (pathname === '/sessions/' && request.method() === 'POST') {
      const payload = request.postDataJSON();
      const now = new Date().toISOString();
      const session = {
        id: payload.id || '44444444-4444-4444-8444-444444444444',
        title: payload.title || '新会话',
        summary: '',
        mode: payload.mode || 'project',
        created_at: now,
        updated_at: now,
        last_message_at: now,
        message_count: 0,
        task_count: 0,
        open_task_count: 0,
      };
      if (!sessions.some((item) => item.id === session.id)) sessions.unshift(session);
      return json(route, session);
    }
    if (pathname === '/sessions/search' && request.method() === 'GET') {
      return json(route, url.searchParams.get('q')?.includes('ComfyUI') ? [{
        message_id: 'search-message',
        session_id: '22222222-2222-4222-8222-222222222222',
        author_model: 'qwen',
        role: 'assistant',
        content: 'Qwen 与 ComfyUI 调用记录',
        snippet: 'Qwen 与 ComfyUI 调用记录',
        created_at: '2026-07-11T09:00:00.000Z',
        title: '本地模型协作排查',
        updated_at: '2026-07-11T09:00:00.000Z',
      }] : []);
    }
    if (/^\/sessions\/[^/]+$/.test(pathname) && request.method() === 'DELETE') {
      const deletedId = pathname.split('/').pop();
      const index = sessions.findIndex((item) => item.id === deletedId);
      if (index >= 0) sessions.splice(index, 1);
      return json(route, { deleted: index >= 0, session_id: deletedId, cleanup: {} });
    }
    if (pathname === `/sessions/${sessionId}/messages`) return json(route, messageFixtures);
    if (pathname === `/sessions/${sessionId}/task-events`) return json(route, taskEventFixtures);
    if (/^\/sessions\/[^/]+\/messages$/.test(pathname)) return json(route, []);
    if (/^\/sessions\/[^/]+\/task-events$/.test(pathname)) return json(route, []);
    if (pathname === '/models/' && request.method() === 'GET') return json(route, modelFixtures);
    if (pathname === `/memory/${sessionId}` && request.method() === 'GET') return json(route, sessionMemory);
    if (pathname === `/memory/${sessionId}` && request.method() === 'PATCH') {
      Object.assign(sessionMemory, request.postDataJSON());
      return json(route, sessionMemory);
    }
    if (pathname === '/agent-runs' && request.method() === 'GET') return json(route, agentRuns);
    if (/^\/agent-runs\/[^/]+$/.test(pathname) && request.method() === 'GET') {
      const runId = pathname.split('/')[2];
      const run = agentRuns.find((item) => item.id === runId) || agentRuns[0];
      return json(route, {
        ...run,
        events: [
          {
            id: 'audit-event-1',
            run_id: runId,
            step_id: 'plan-2',
            event_type: 'tool_call',
            payload: {
              model: 'deepseek',
              tool_name: 'plugin__comfyui__run_workflow',
              status: 'running',
            },
            created_at: '2026-07-15T00:00:30Z',
          },
          {
            id: 'audit-event-2',
            run_id: runId,
            step_id: 'plan-2',
            event_type: 'message_complete',
            payload: {
              author_model: 'deepseek',
              content_preview: '已将工作流参数交给 ComfyUI。',
            },
            created_at: '2026-07-15T00:00:36Z',
          },
        ],
        tool_calls: [{
          id: 'audit-tool-1',
          run_id: runId,
          model: 'deepseek',
          tool_name: 'plugin__comfyui__run_workflow',
          status: 'completed',
          side_effect: true,
          verification_status: 'unverified',
          evidence: [{ kind: 'tool_result_digest', sha256: 'a'.repeat(64) }],
          arguments: { name: 'cover', overrides: { 6: { seed: 42 } } },
          result: { prompt_id: 'cover' },
          error: null,
          created_at: '2026-07-15T00:00:30Z',
          updated_at: '2026-07-15T00:00:35Z',
        }],
      });
    }
    if (pathname === '/automations/status' && request.method() === 'GET') {
      return json(route, {
        scheduler_running: true,
        active_workers: backgroundJobs.filter((item) => item.status === 'running').length,
        concurrency: 1,
        enabled_schedules: automationSchedules.filter((item) => item.enabled).length,
        queued_jobs: backgroundJobs.filter((item) => item.status === 'queued').length,
        running_jobs: backgroundJobs.filter((item) => item.status === 'running').length,
        review_items: backgroundJobs.filter((item) => item.completed_at && !item.reviewed_at).length,
      });
    }
    if (pathname === '/automations/schedules' && request.method() === 'GET') {
      return json(route, automationSchedules);
    }
    if (pathname === '/automations/schedules' && request.method() === 'POST') {
      const payload = request.postDataJSON();
      const now = new Date().toISOString();
      const created = {
        id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
        ...payload,
        enabled: true,
        permission_snapshot: {
          policy_version: 1,
          mode: 'read_only',
          allowed_tools: [],
          captured_at: now,
        },
        last_run_at: null,
        last_status: null,
        last_error: null,
        created_at: now,
        updated_at: now,
      };
      automationSchedules.unshift(created);
      return json(route, created, 201);
    }
    if (/^\/automations\/schedules\/[^/]+$/.test(pathname) && request.method() === 'PATCH') {
      const id = pathname.split('/')[3];
      const index = automationSchedules.findIndex((item) => item.id === id);
      automationSchedules[index] = { ...automationSchedules[index], ...request.postDataJSON(), updated_at: new Date().toISOString() };
      return json(route, automationSchedules[index]);
    }
    if (/^\/automations\/schedules\/[^/]+$/.test(pathname) && request.method() === 'DELETE') {
      const id = pathname.split('/')[3];
      const index = automationSchedules.findIndex((item) => item.id === id);
      if (index >= 0) automationSchedules.splice(index, 1);
      return json(route, { deleted: index >= 0, id });
    }
    if (/^\/automations\/schedules\/[^/]+\/run$/.test(pathname) && request.method() === 'POST') {
      const schedule = automationSchedules.find((item) => item.id === pathname.split('/')[3]);
      const now = new Date().toISOString();
      const job = {
        id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
        schedule_id: schedule.id,
        schedule_name: schedule.name,
        session_id: sessionId,
        agent_run_id: null,
        trigger_type: 'manual',
        status: 'queued',
        action: { content: schedule.prompt, targets: schedule.targets, mode: schedule.mode },
        permission_snapshot: schedule.permission_snapshot,
        max_budget_usd: schedule.max_budget_usd,
        budget_spent_usd: 0,
        scheduled_for: now,
        attempt: 0,
        summary: '',
        error: null,
        reviewed_at: null,
        created_at: now,
        updated_at: now,
        started_at: null,
        completed_at: null,
      };
      backgroundJobs.unshift(job);
      return json(route, job, 202);
    }
    if (pathname === '/automations/jobs' && request.method() === 'GET') return json(route, backgroundJobs);
    if (/^\/automations\/jobs\/[^/]+\/cancel$/.test(pathname) && request.method() === 'POST') {
      const id = pathname.split('/')[3];
      const job = backgroundJobs.find((item) => item.id === id);
      Object.assign(job, { status: 'cancelled', error: '用户取消', completed_at: new Date().toISOString() });
      return json(route, job);
    }
    if (/^\/automations\/jobs\/[^/]+\/review$/.test(pathname) && request.method() === 'POST') {
      const id = pathname.split('/')[3];
      const job = backgroundJobs.find((item) => item.id === id);
      Object.assign(job, { reviewed_at: new Date().toISOString() });
      return json(route, job);
    }
    if (pathname === '/mcp-connections' && request.method() === 'GET') return json(route, mcpConnections);
    if (pathname === '/mcp-connections' && request.method() === 'POST') {
      const payload = request.postDataJSON();
      const created = { id: 'remote-mcp', ...payload, has_token: Boolean(payload.token), token: undefined, enabled: true, allowed_tools: null, tools: [], server_info: {}, status: 'disconnected', error: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      mcpConnections.unshift(created);
      return json(route, created, 201);
    }
    if (/^\/mcp-connections\/[^/]+$/.test(pathname) && request.method() === 'PATCH') {
      const id = pathname.split('/')[2];
      const index = mcpConnections.findIndex((item) => item.id === id);
      mcpConnections[index] = { ...mcpConnections[index], ...request.postDataJSON() };
      return json(route, mcpConnections[index]);
    }
    if (/^\/mcp-connections\/[^/]+\/refresh$/.test(pathname) && request.method() === 'POST') {
      return json(route, mcpConnections.find((item) => item.id === pathname.split('/')[2]));
    }
    if (/^\/approvals\/[^/]+\/decision$/.test(pathname) && request.method() === 'POST') {
      return json(route, { id: pathname.split('/')[2], decision: request.postDataJSON().decision, resolved: true });
    }
    if (pathname === `/knowledge/${sessionId}/attachments` && request.method() === 'GET') return json(route, knowledgeAttachments);
    if (pathname === `/knowledge/${sessionId}/attachments` && request.method() === 'POST') {
      const attachment = {
        id: '55555555-5555-4555-8555-555555555555',
        session_id: sessionId,
        name: 'project-notes.md',
        content_type: 'text/markdown',
        size_bytes: 32,
        status: 'ready',
        chunk_count: 1,
        created_at: new Date().toISOString(),
      };
      knowledgeAttachments.push(attachment);
      return json(route, attachment, 201);
    }
    if (/^\/knowledge\/[^/]+\/attachments$/.test(pathname) && request.method() === 'GET') {
      return json(route, []);
    }
    if (/^\/knowledge\/[^/]+\/attachments\/[^/]+\/preview$/.test(pathname)) {
      const attachmentId = pathname.split('/')[4];
      const attachment = knowledgeAttachments.find((item) => item.id === attachmentId);
      return json(route, {
        attachment,
        kind: 'text',
        chunks: [{ locator: '全文', content: '项目资料需要可追溯引用。' }],
        truncated: false,
      });
    }
    if (pathname === `/knowledge/${sessionId}/search`) return json(route, [{
      attachment_id: '55555555-5555-4555-8555-555555555555',
      source_name: 'project-notes.md',
      locator: '全文',
      content: '项目资料需要可追溯引用。',
      citation: 'project-notes.md · 全文',
      score: 0.92,
      match_types: ['全文', '语义'],
    }]);
    if (/^\/models\/[^/]+\/verify$/.test(pathname)) return json(route, { ok: true, message: '模拟连接正常', sample: 'pong' });
    if (pathname === '/tasks/' && request.method() === 'GET') return json(route, tasks);
    if (pathname === '/tasks/' && request.method() === 'POST') {
      const payload = request.postDataJSON();
      const task = { id: `task-${tasks.length + 1}`, status: 'backlog', ...payload };
      tasks.push(task);
      return json(route, task);
    }
    if (/^\/tasks\/[^/]+$/.test(pathname) && request.method() === 'PATCH') {
      const taskId = pathname.split('/').pop();
      const index = tasks.findIndex((task) => task.id === taskId);
      const updated = { ...tasks[index], ...request.postDataJSON() };
      if (index >= 0) tasks[index] = updated;
      return json(route, updated);
    }
    if (pathname === '/artifacts/' && request.method() === 'GET') {
      return json(route, artifactFixtures.map((artifact) => (
        artifact.path === 'notes/deleted.md' && historyArtifactRestored
          ? { ...artifact, availability: 'current', recoverable: true }
          : artifact
      )));
    }
    if (pathname === '/artifacts/generated/comfyui/cover.png/native-location' && request.method() === 'GET') {
      return json(route, {
        path: '/home/test/.local/share/aetherchat/artifacts/11111111-1111-4111-8111-111111111111/generated/comfyui/cover.png',
        artifact_path: 'generated/comfyui/cover.png',
        display_name: 'cover.png',
        mime_type: 'image/png',
        size_bytes: 2048,
      });
    }
    if (pathname === '/artifacts/generated/comfyui/cover.png/content' && request.method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'image/png',
        body: Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII=',
          'base64',
        ),
      });
    }
    if (pathname === '/artifacts/README.md/preview' && request.method() === 'GET') {
      return json(route, {
        path: 'README.md', mime_type: 'text/markdown', mode: 'markdown',
        content: '# AetherChat\n\n安全工件预览。', truncated: false,
      });
    }
    if (pathname === '/artifacts/README.md/history' && request.method() === 'GET') return json(route, artifactVersions);
    if (pathname === '/artifacts/README.md/diff' && request.method() === 'GET') {
      return json(route, { path: 'README.md', from_version: 1, to_version: 2, diff: '-# AetherChat\n+# AetherChat\n+\n+安全工件预览。' });
    }
    if (pathname === '/artifacts/README.md/restore' && request.method() === 'POST') {
      return json(route, { path: 'README.md', version: 3, parent_version: 1 });
    }
    if (pathname === '/artifacts/notes/deleted.md/preview' && request.method() === 'GET') {
      return json(route, {
        path: 'notes/deleted.md', mime_type: 'text/markdown', mode: 'markdown',
        content: '# Deleted\n\n可恢复的历史内容。', truncated: false,
        availability: historyArtifactRestored ? 'current' : 'history_only',
        recoverable_version: historyArtifactRestored ? null : 1,
      });
    }
    if (pathname === '/artifacts/notes/deleted.md/history' && request.method() === 'GET') {
      return json(route, deletedArtifactVersions);
    }
    if (pathname === '/artifacts/notes/deleted.md/restore' && request.method() === 'POST') {
      historyArtifactRestored = true;
      return json(route, { path: 'notes/deleted.md', version: 2, parent_version: 1 });
    }
    if (pathname === '/artifacts/write' && request.method() === 'POST') {
      return json(route, { path: 'README.md', version: 3, current_hash: 'new-hash' });
    }
    if (pathname === '/artifacts/README.md/export' && request.method() === 'GET') {
      const format = url.searchParams.get('format') || 'md';
      return route.fulfill({
        status: 200,
        contentType: format === 'docx' ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' : 'text/markdown',
        headers: {
          'Content-Disposition': `attachment; filename="README.${format}"`,
          'Access-Control-Expose-Headers': 'Content-Disposition',
        },
        body: format === 'md' ? '# AetherChat\n' : 'mock-office-binary',
      });
    }
    if (pathname === '/artifacts/merge/proposals' && request.method() === 'GET') return json(route, []);
    if (pathname === '/tools/runs' && request.method() === 'GET') return json(route, []);
    if (pathname === '/tools/' && request.method() === 'GET') return json(route, []);
    if (pathname === '/plugins/' && request.method() === 'GET') return json(route, plugins);
    if (pathname === '/plugins/comfyui/runtime/status' && request.method() === 'GET') {
      return json(route, {
        ready: true,
        runtime: { ready: true },
        diagnostics: { message: 'ready' },
        selection: { image_model: 'qwen_image_2512_fp8_e4m3fn.safetensors', video_pipeline: 'wan-i2v' },
        catalog: {
          image_models: [
            { name: 'RealVisXL_V5.0_Lightning_fp16.safetensors', kind: 'checkpoint', recommended: true },
            { name: 'qwen_image_2512_fp8_e4m3fn.safetensors', kind: 'diffusion_model', recommended: false },
          ],
          video_pipelines: [{
            id: 'wan-i2v',
            label: 'Wan I2V（图生视频）',
            installed: true,
            available: true,
            missing: [],
            requires_input_image: false,
            warning: '',
            models: [{
              name: 'Wan2.2_Remix_NSFW_i2v_14b_high_lighting_fp8_e4m3fn_v3.0.safetensors',
              label: 'Wan 2.2 Remix I2V 14B（双阶段）',
              installed: true,
              companion_models: ['Wan2.2_Remix_NSFW_i2v_14b_low_lighting_fp8_e4m3fn_v3.0.safetensors'],
            }],
          }],
        },
      });
    }
    if (/^\/plugins\/[^/]+$/.test(pathname) && request.method() === 'PATCH') {
      const pluginId = pathname.split('/').pop();
      const index = plugins.findIndex((plugin) => plugin.id === pluginId);
      const updated = { ...plugins[index], ...request.postDataJSON() };
      if (index >= 0) plugins[index] = updated;
      return json(route, updated);
    }
    if (/^\/plugins\/[^/]+\/invoke$/.test(pathname) && request.method() === 'POST') {
      const pluginId = pathname.split('/')[2];
      const payload = request.postDataJSON();
      return json(route, {
        plugin_id: pluginId,
        plugin_name: plugins.find((plugin) => plugin.id === pluginId)?.name || pluginId,
        action: payload.action,
        status: 'success',
        result: { healthy: true, artifacts: 1, tasks: tasks.length },
        created_at: '2026-01-01T00:00:00.000Z',
      });
    }
    if (pathname.startsWith('/export/session/') && url.searchParams.get('format') === 'json') {
      return json(route, { session_id: sessionId, artifacts: artifactFixtures, tools: [], tool_runs: [] });
    }
    if (pathname.startsWith('/export/session/')) {
      return route.fulfill({ status: 200, contentType: 'text/markdown; charset=utf-8', body: '# AetherChat mock export\n' });
    }
    if (pathname === '/diagnostics/bundle' && request.method() === 'POST') {
      return route.fulfill({
        status: 200,
        contentType: 'application/zip',
        headers: {
          'Content-Disposition': 'attachment; filename="aetherchat-diagnostics-20260811T000000Z.zip"',
          'Access-Control-Expose-Headers': 'Content-Disposition',
          'X-Content-Type-Options': 'nosniff',
        },
        body: 'mock-redacted-diagnostics',
      });
    }
    if (pathname === '/auth/token') return json(route, { access_token: 'mock-token', token_type: 'bearer' });

    return json(route, {});
  });
}

async function openSettingsTab(page: Page, tab: string) {
  const settingsPage = page.getByTestId('settings-page');
  if (!(await settingsPage.isVisible())) {
    await page.getByTitle('设置').or(page.getByTitle('Settings')).click();
    await expect(settingsPage).toBeVisible();
  }
  if (tab !== '设置' && tab !== 'Settings') {
    await page.getByRole('tab', { name: tab, exact: true }).click();
  }
}

async function acceptConfirmDialog(page: Page) {
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '确定' }).click();
}

/** Phase-2 rail defaults: expand advanced panels so legacy e2e panel titles stay reachable. */
async function installWorkspaceUiDefaults(page: Page, options?: {
  dismissFirstRun?: boolean;
  expandAdvancedRail?: boolean;
}) {
  const dismissFirstRun = options?.dismissFirstRun ?? true;
  const expandAdvancedRail = options?.expandAdvancedRail ?? true;
  await page.addInitScript(({ dismiss, expand }) => {
    if (dismiss) {
      localStorage.setItem('aetherchat.first_run.dismissed.v1', '1');
    } else {
      localStorage.removeItem('aetherchat.first_run.dismissed.v1');
    }
    if (expand) {
      localStorage.setItem('aetherchat.activity_rail.advanced.v1', '1');
    } else {
      localStorage.removeItem('aetherchat.activity_rail.advanced.v1');
    }
  }, { dismiss: dismissFirstRun, expand: expandAdvancedRail });
}

async function expectNoHorizontalPageOverflow(page: Page) {
  const overflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    body: document.body.scrollWidth - document.body.clientWidth,
  }));
  expect(overflow.document, `document overflowed horizontally by ${overflow.document}px`).toBeLessThanOrEqual(1);
  expect(overflow.body, `body overflowed horizontally by ${overflow.body}px`).toBeLessThanOrEqual(1);
}

test.describe('AetherChat Frontend E2E', () => {
  test.beforeEach(async ({ page }) => {
    page.on('pageerror', (error) => console.error(`Browser page error: ${error.message}`));
    await installWorkspaceUiDefaults(page);
    await installMockWebSocket(page);
    await installMockDesktopBridge(page);
    await installMockApi(page);
    await page.goto('/');
    await expect(page.locator('textarea[data-aether-composer]')).toBeVisible();
    await expect.poll(() => page.evaluate(() => (
      window as typeof window & { __aetherWsReady?: () => boolean }
    ).__aetherWsReady?.() || false)).toBe(true);
  });

  test('loads the current desktop workspace', async ({ page }) => {
    await expect(page).toHaveTitle(/AetherChat/);
    await expect(page.getByText('AetherChat', { exact: true })).toBeVisible();
    await expect(page.getByText('模式:')).toBeVisible();
    await expect(page.getByRole('button', { name: '休闲' })).toBeVisible();
    await expect(page.getByRole('button', { name: '圆桌' })).toBeVisible();
    await expect(page.getByRole('button', { name: '项目', exact: true })).toBeVisible();
    await expect(page.getByText('已连接', { exact: true })).toBeVisible();
  });

  test('shows and clears bounded run queue state in the desktop status bar', async ({ page }) => {
    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
      type: 'run_queued',
      dispatch_id: 'dispatch-queued-e2e',
      queue_position: 2,
      queue_capacity: 64,
    }));
    await expect(page.locator('footer')).toContainText('运行排队 #2');
    await expect(page.getByText('运行已排队，前方位置 2')).toBeVisible();

    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
      type: 'run_dispatch_started',
      dispatch_id: 'dispatch-queued-e2e',
    }));
    await expect(page.locator('footer')).not.toContainText('运行排队');
    await expect(page.getByText('排队结束，运行已获得执行槽')).toBeVisible();

    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
        type: 'run_queued',
        dispatch_id: 'dispatch-cancelled-e2e',
        queue_position: 1,
      }));
    await page.getByRole('button', { name: '取消排队运行' }).click();
    await expect.poll(() => page.evaluate(() => (
      window as typeof window & {
        __aetherWsActions?: Array<{ type?: string; dispatch_id?: string }>;
      }
    ).__aetherWsActions?.some((action) => (
      action.type === 'interrupt'
      && action.dispatch_id === 'dispatch-cancelled-e2e'
    )))).toBe(true);
    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
        type: 'run_cancelled',
        dispatch_id: 'dispatch-cancelled-e2e',
        detail: '排队中的运行已取消',
      }));
    await expect(page.locator('footer')).not.toContainText('运行排队');
    await expect(page.getByText('排队中的运行已取消')).toBeVisible();
  });

  test('shows one compact Claude/Codex-style working strip that updates in place', async ({ page }) => {
    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
      type: 'provider_status',
      model: 'deepseek',
      phase: 'requesting',
      detail: '上下文已整理，正在等待模型返回首个 token',
      context_messages: 12,
      context_tokens: 4096,
      tool_count: 5,
      tool_schema_tokens: 768,
    }));
    const status = page.locator('[data-model-working="true"]');
    await expect(status).toHaveCount(1);
    await expect(status).toHaveAttribute('role', 'status');
    await expect(status).toHaveAttribute('data-phase', 'requesting');
    await expect(status).toContainText('deepseek · 正在思考');
    await expect(page.getByText(/上下文 12 条/)).toHaveCount(0);

    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
      type: 'provider_status',
      model: 'deepseek',
      phase: 'streaming',
      detail: '已收到首个 token，正在生成回复',
      context_messages: 12,
      context_tokens: 4096,
      tool_count: 5,
      first_token_ms: 1234,
    }));
    await expect(status).toHaveCount(1);
    await expect(status).toHaveAttribute('data-phase', 'streaming');
    await expect(status).toContainText('deepseek · 正在组织答复');

    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
      type: 'tool_call',
      model: 'deepseek',
      tool_name: 'plugin__comfyui__run_workflow',
    }));
    await expect(status).toHaveCount(1);
    await expect(status).toHaveAttribute('data-phase', 'tool');
    await expect(status).toContainText('deepseek · 正在使用 run_workflow');
    await expect(page.getByText(/预计剩余/)).toHaveCount(0);

    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
      type: 'message_complete',
      message: {
        id: 'working-strip-complete',
        session_id: '11111111-1111-4111-8111-111111111111',
        author_model: 'deepseek',
        role: 'assistant',
        content: '状态条完成后只留下最终答复。',
        created_at: new Date().toISOString(),
      },
    }));
    await expect(status).toHaveCount(0);
    await expect(page.getByText('状态条完成后只留下最终答复。')).toBeVisible();
  });

  test('keeps internal model history and live reasoning out of the chat feed', async ({ page }) => {
    await expect(page.getByText('INTERNAL_MODEL_HISTORY_REQUEST')).toHaveCount(0);
    await expect(page.getByText('INTERNAL_TOOL_RESULT_HISTORY')).toHaveCount(0);
    await expect(page.getByText(/思考过程（安全摘要）/)).toHaveCount(0);
    await page.evaluate(() => {
      const emit = (
        window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
      ).__emitAetherWs;
      emit?.({
        type: 'reasoning_step',
        model: 'deepseek',
        reset: true,
        step: {
          phase: 'analysis',
          detail: '已整理 deepseek 的上下文。',
          ts: new Date().toISOString(),
        },
      });
      emit?.({
        type: 'reasoning_step',
        model: 'GLM',
        reset: true,
        step: {
          phase: 'analysis',
          detail: '已整理 GLM 的上下文。',
          ts: new Date().toISOString(),
        },
      });
      emit?.({
        type: 'message_complete',
        message: {
          id: 'legacy-model-request-message',
          session_id: '11111111-1111-4111-8111-111111111111',
          author_model: 'Qwen',
          role: 'assistant',
          content: '让我先检查历史请求。\n\n让我继续调用工具。\n\n最终结果应保留。',
          reasoning_summary: [
            { phase: 'tool', detail: '已选择调用测试工具。', ts: new Date().toISOString() },
            { phase: 'final', detail: '已整理最终答复。', ts: new Date().toISOString() },
          ],
          created_at: new Date().toISOString(),
        },
      });
    });
    await expect(page.getByText('已整理 deepseek 的上下文。')).toHaveCount(0);
    await expect(page.getByText('已整理 GLM 的上下文。')).toHaveCount(0);
    await expect(page.getByText('实时更新')).toHaveCount(0);
    await expect(page.getByText('让我先检查历史请求。')).toHaveCount(0);
    await expect(page.getByText('让我继续调用工具。')).toHaveCount(0);
    await expect(page.getByText('最终结果应保留。')).toBeVisible();
    await expect(page.getByText('继续数据库任务')).toBeVisible();
    await expect(page.getByText('已恢复上次上下文。')).toBeVisible();
  });

  test('shows a factual execution receipt and opens the run audit', async ({ page }) => {
    const receipt = page.getByLabel('实际执行与交付');
    await expect(receipt).toBeVisible();
    await expect(receipt.getByText('已记录工具结果；请在运行审计中确认核验状态')).toBeVisible();
    await receipt.getByRole('button').first().click();
    await expect(receipt.getByText('generate_image')).toBeVisible();
    await expect(receipt.getByText('交付文件 1')).toBeVisible();
    await receipt.getByRole('button', { name: '查看运行审计' }).click();
    await expect(page.getByRole('heading', { name: 'Agent 运行记录' })).toBeVisible();
  });

  test('marks legacy browser intents without desktop receipts as unverified', async ({ page }) => {
    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
      type: 'message_complete',
      message: {
        id: 'legacy-browser-message',
        session_id: '11111111-1111-4111-8111-111111111111',
        author_model: 'Qwen',
        role: 'assistant',
        content: '我已经帮你打开了浏览器。',
        reasoning_summary: [
          {
            phase: 'analysis',
            detail: '已筛选 1 个与请求相关的可用工具。',
            ts: '2026-08-01T10:05:47.000Z',
          },
          {
            phase: 'tool',
            detail: '已选择调用 plugin__browser_links__open_url，等待授权或本机执行结果。',
            ts: '2026-08-01T10:05:48.000Z',
          },
          {
            phase: 'result',
            detail: '工具 plugin__browser_links__open_url 执行成功，正在依据真实结果继续处理。',
            ts: '2026-08-01T10:05:52.000Z',
          },
          {
            phase: 'final',
            detail: '已核对模型输出与工具结果，正在整理最终答复。',
            ts: '2026-08-01T10:05:53.000Z',
          },
        ],
        created_at: '2026-08-01T10:05:53.000Z',
      },
    }));

    const warning = page.locator(
      '[aria-label="实际执行与交付"][data-execution-status="unverified"]',
    ).filter({ hasText: '历史外部导航缺少桌面执行凭证' });
    await expect(warning).toBeVisible();
    await expect(warning.getByText('历史外部导航缺少桌面执行凭证')).toBeVisible();
    await expect(warning.getByText(/不能证明浏览器已启动/)).toBeVisible();
    await expect(warning.getByText('本次处理已完成')).toHaveCount(0);
  });

  test('accepts a desktop quick-entry intent and focuses the composer', async ({ page }) => {
    await page.evaluate(() => (
      window as typeof window & { __emitAetherLaunchIntent?: (intent: unknown) => void }
    ).__emitAetherLaunchIntent?.({
      id: 'launch-e2e-1',
      action: 'compose',
      prompt: '对比 Claude 并完善交付闭环',
      mode: 'casual',
      newConversation: false,
      source: 'protocol',
    }));
    const composer = page.locator('textarea[data-aether-composer]');
    await expect(composer).toHaveValue('对比 Claude 并完善交付闭环');
    await expect(composer).toBeFocused();
    await expect(page.getByRole('button', { name: '休闲' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByText('已从 AetherChat 链接载入输入内容')).toBeVisible();
  });

  test('executes a model browser intent and returns a desktop launch receipt', async ({ page }) => {
    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
      type: 'desktop_action_required',
      action_id: 'browser-action-1',
      desktop_action: {
        kind: 'open_external',
        url: 'https://www.youtube.com',
        browser: 'firefox',
      },
    }));

    await expect(page.getByText('Firefox 已接受打开链接请求')).toBeVisible();
    await expect.poll(() => page.evaluate(() => {
      const testWindow = window as typeof window & {
        __aetherDesktopActions?: Array<{ type: string; payload?: unknown }>;
        __aetherWsActions?: Array<Record<string, unknown>>;
      };
      return {
        desktop: testWindow.__aetherDesktopActions?.find(
          (item) => item.type === 'open-external',
        ),
        websocket: testWindow.__aetherWsActions?.find(
          (item) => item.type === 'desktop_action_result',
        ),
      };
    })).toMatchObject({
      desktop: {
        type: 'open-external',
        payload: {
          browser: 'firefox',
          launcher: 'firefox',
          url: 'https://www.youtube.com/',
          verification: 'launcher_accepted',
        },
      },
      websocket: {
        type: 'desktop_action_result',
        action_id: 'browser-action-1',
        success: true,
        receipt: {
          browser: 'firefox',
          launcher: 'firefox',
          verification: 'launcher_accepted',
        },
      },
    });
  });

  test('switches collaboration modes', async ({ page }) => {
    await expect(page.getByRole('button', { name: '项目', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: '休闲' }).click();
    await expect(page.getByRole('button', { name: '休闲' })).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: '圆桌' }).click();
    await expect(page.getByRole('button', { name: '圆桌' })).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: '项目', exact: true }).click();
    await expect(page.getByRole('button', { name: '项目', exact: true })).toHaveAttribute('aria-pressed', 'true');
  });

  test('restores chat history and task records from the workspace tabs', async ({ page }) => {
    await expect(page.getByText('已恢复上次上下文。')).toBeVisible();

    await page.getByRole('tab', { name: '历史聊天' }).click();
    await expect(page.getByRole('heading', { name: '历史聊天' })).toBeVisible();
    await expect(page.getByRole('button', { name: /本地模型协作排查.*8 条消息/ })).toBeVisible();
    await expect(page.getByText('8 条消息')).toBeVisible();

    await page.getByRole('tab', { name: '任务记录' }).click();
    await expect(page.getByRole('heading', { name: '当前任务' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '变更记录' })).toBeVisible();
    await expect(page.getByText('更新任务 · 进行中')).toBeVisible();
    await page.screenshot({ path: '../output/playwright/aetherchat-task-history.png', fullPage: true });

    await page.getByRole('tab', { name: '当前对话' }).click();
    await expect(page.getByText('已恢复上次上下文。')).toBeVisible();
  });

  test('opens generated files and reveals their containing directory', async ({ page }) => {
    await expect(page.getByText('本次生成的文件')).toBeVisible();
    await expect(page.getByText('cover.png', { exact: true })).toBeVisible();
    await expect(page.getByText('已保存到本机')).toBeVisible();
    await expect(page.getByText(/保存位置：.*generated\/comfyui\/cover\.png/)).toHaveCount(0);
    await page.getByRole('button', { name: '预览文件 cover.png' }).click();
    await expect(page.getByRole('img', { name: 'cover.png' })).toBeVisible();
    await page.getByRole('button', { name: '打开文件 cover.png' }).click();
    await expect(page.getByText('已打开 cover.png')).toBeVisible();
    await page.getByRole('button', { name: '打开文件所在目录 cover.png' }).click();
    await expect(page.getByText('已在文件夹中显示 cover.png')).toBeVisible();

    const actions = await page.evaluate(() => (
      window as typeof window & {
        __aetherDesktopActions?: Array<{ type: string; payload?: unknown }>;
      }
    ).__aetherDesktopActions);
    expect(actions).toContainEqual({
      type: 'artifact-open',
      payload: {
        selectedSessionId: sessionId,
        path: 'generated/comfyui/cover.png',
      },
    });
    expect(actions).toContainEqual({
      type: 'artifact-reveal',
      payload: {
        selectedSessionId: sessionId,
        path: 'generated/comfyui/cover.png',
      },
    });
  });

  test('searches all cached chats and deletes a recent session', async ({ page }) => {
    await page.getByRole('tab', { name: '历史聊天' }).click();
    await page.getByPlaceholder('搜索全部聊天内容、会话标题或 Agent').fill('ComfyUI');
    await expect(page.getByTestId('chat-search-results')).toContainText('本地模型协作排查');
    await expect(page.getByTestId('chat-search-results')).toContainText('Qwen 与 ComfyUI 调用记录');

    await page.getByPlaceholder('搜索全部聊天内容、会话标题或 Agent').fill('');
    const deleteRequest = page.waitForRequest((request) => (
      new URL(request.url()).pathname === '/sessions/22222222-2222-4222-8222-222222222222'
      && request.method() === 'DELETE'
    ));
    await page
      .locator('#aether-main-content > section')
      .getByRole('button', { name: '删除会话 本地模型协作排查' })
      .click();
    await acceptConfirmDialog(page);
    await deleteRequest;
    await expect(page.getByText('会话“本地模型协作排查”已删除')).toBeVisible();
    await expect(page.getByText('本地模型协作排查', { exact: true })).toHaveCount(0);
  });

  test('sends image and video generation from composer chips with full model names', async ({ page }) => {
    const composer = page.locator('textarea[data-aether-composer]');
    await expect(page.getByRole('button', { name: '代码', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '图片生成', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '视频生成', exact: true })).toBeVisible();

    await page.getByRole('button', { name: '图片生成', exact: true }).click();
    await page.getByRole('option', { name: /Qwen Image/ }).click();
    await composer.fill('雨夜街头的警察');
    await page.getByRole('button', { name: '发送' }).click();
    await expect(page.getByText('使用 qwen_image_2512_fp8_e4m3fn.safetensors 生成图片')).toBeVisible();

    await page.getByRole('button', { name: '视频生成', exact: true }).click();
    await expect(page.getByRole('button', { name: 'High' })).toBeVisible();
    await page.getByRole('button', { name: 'Low' }).click();
    await composer.fill('人物慢慢转身');
    await page.getByRole('button', { name: '发送' }).click();

    await expect.poll(() => page.evaluate(() => {
      const actions = (window as typeof window & { __aetherWsActions?: Array<{ type?: string; content?: string }> }).__aetherWsActions || [];
      return actions.filter((action) => action.type === 'post_message').map((action) => action.content);
    })).toEqual(expect.arrayContaining([
      '使用 qwen_image_2512_fp8_e4m3fn.safetensors 生成图片\n雨夜街头的警察',
      '使用 Wan2.2_Remix_NSFW_i2v_14b_low_lighting_fp8_e4m3fn_v3.0.safetensors 生成视频\n人物慢慢转身',
    ]));
  });

  test('sends a message through WebSocket and renders it in the feed', async ({ page }) => {
    const composer = page.locator('textarea[data-aether-composer]');
    await composer.fill('请 @deepseek 开始分析任务');
    await page.getByRole('button', { name: '发送' }).click();

    await expect(page.getByText('请 @deepseek 开始分析任务')).toBeVisible();
    await expect.poll(() => page.evaluate(() => (
      (window as typeof window & { __aetherWsActions?: Array<{ type?: string; request_id?: string }> }).__aetherWsActions
        ?.some((action) => action.type === 'post_message' && Boolean(action.request_id))
    ))).toBe(true);
  });

  test('shows that a message was queued as an active-run steer', async ({ page }) => {
    const composer = page.locator('textarea[data-aether-composer]');
    await composer.fill('运行转向：改用方案 B');
    await page.getByRole('button', { name: '发送' }).click();

    await expect(page.getByText('运行转向：改用方案 B')).toBeVisible();
    await expect(page.getByText('已加入运行转向，将在下一安全边界应用')).toBeVisible();
  });

  test('uploads a project document and sends its attachment reference', async ({ page }) => {
    await page.locator('input[type="file"]').setInputFiles({
      name: 'project-notes.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from('项目资料需要可追溯引用。'),
    });
    await expect(page.getByText('project-notes.md')).toBeVisible();
    await page.locator('[data-aether-composer]').fill('请依据附件总结');
    await page.getByRole('button', { name: '发送' }).click();

    const actions = await page.evaluate(() => (
      (window as typeof window & { __aetherWsActions?: Array<Record<string, unknown>> }).__aetherWsActions
    ));
    expect(actions?.at(-1)).toMatchObject({
      type: 'post_message',
      content: '请依据附件总结 [@文档:project-notes.md]',
      attachment_ids: ['55555555-5555-4555-8555-555555555555'],
      execution_strategy: 'planned',
    });

    await openSettingsTab(page, '资料');
    await page.getByPlaceholder('搜索文件正文、表格或幻灯片').fill('可追溯引用');
    await page.getByRole('button', { name: '检索' }).click();
    await expect(page.getByText('project-notes.md · 全文')).toBeVisible();
    await expect(page.getByText('92%')).toBeVisible();
    await expect(page.getByText('全文 · 向量')).toBeVisible();
    await expect(page.getByLabel('资料处理进度 100%')).toBeVisible();
    await page.getByRole('button', { name: '预览 project-notes.md' }).click();
    await expect(page.getByRole('region', { name: '资料安全预览' })).toContainText('项目资料需要可追溯引用');
  });

  test('uses a Claude/Codex-style approval chooser with keyboard control', async ({ page }) => {
    await page.locator('[data-aether-composer]').fill('这个操作需要授权');
    await page.getByRole('button', { name: '发送' }).click();
    const card = page.getByRole('article', { name: '模型操作授权' });
    await expect(card.getByText('允许使用 run_command？')).toBeVisible();
    await expect(card.getByText('允许模型运行沙箱命令？')).toBeVisible();
    await expect(card.getByText('npm test', { exact: true })).toBeVisible();
    await expect(card.getByText('command_execution')).toBeVisible();
    const chooser = card.getByRole('listbox', { name: '模型操作授权' });
    const options = chooser.getByRole('option');
    await expect(chooser).toBeFocused();
    await expect(options).toHaveCount(3);
    await expect(options.nth(0)).toContainText('允许');
    await expect(options.nth(1)).toContainText('本会话允许');
    await expect(options.nth(2)).toContainText('拒绝');
    await expect(options.nth(0)).toHaveAttribute('aria-selected', 'true');
    await chooser.press('ArrowDown');
    await expect(options.nth(1)).toHaveAttribute('aria-selected', 'true');
    await chooser.press('ArrowUp');

    const decisionRequest = page.waitForRequest((request) => request.url().includes('/approvals/') && request.url().endsWith('/decision'));
    await chooser.press('Enter');
    const request = await decisionRequest;
    expect(request.postDataJSON()).toEqual({ session_id: sessionId, decision: 'allow_once' });
    await expect(page.getByRole('article', { name: '模型操作授权' }).getByText('已允许一次')).toBeVisible();
    await expect(page.locator('[data-aether-composer]')).toBeVisible();
  });

  test('collects rejection feedback and returns it with the denial', async ({ page }) => {
    await page.locator('[data-aether-composer]').fill('这个操作需要授权');
    await page.getByRole('button', { name: '发送' }).click();
    const prompt = page.getByRole('article', { name: '模型操作授权' });
    const chooser = prompt.getByRole('listbox', { name: '模型操作授权' });
    await chooser.press('Escape');

    const feedback = prompt.getByPlaceholder('输入反馈；留空将直接拒绝');
    await expect(feedback).toBeFocused();
    await feedback.fill('不要运行命令，改为只读检查配置');
    const decisionRequest = page.waitForRequest((request) => request.url().includes('/approvals/') && request.url().endsWith('/decision'));
    await feedback.press('Enter');
    const request = await decisionRequest;
    expect(request.postDataJSON()).toEqual({
      session_id: sessionId,
      decision: 'deny',
      feedback: '不要运行命令，改为只读检查配置',
    });
    const record = page.getByRole('article', { name: '模型操作授权' });
    await expect(record.getByText('已拒绝')).toBeVisible();
    await expect(record.getByText('反馈：不要运行命令，改为只读检查配置')).toBeVisible();
  });

  test('reviews and edits persistent session memory', async ({ page }) => {
    await openSettingsTab(page, '记忆');
    await expect(page.getByRole('heading', { name: '本机会话记忆' })).toBeVisible();
    await expect(page.locator('label').filter({ hasText: '关键决定' }).locator('textarea')).toHaveValue('决定使用 SQLite');
    await expect(page.getByText('最近对话摘要：')).toBeVisible();

    const note = page.getByPlaceholder('例如：本项目使用 TypeScript，所有危险操作必须逐次授权。');
    await note.fill('所有危险操作必须明确授权');
    const saveRequest = page.waitForRequest((request) => request.url().endsWith(`/memory/${sessionId}`) && request.method() === 'PATCH');
    await page.getByRole('button', { name: '保存记忆' }).click();
    const request = await saveRequest;
    expect(request.postDataJSON().project_note).toBe('所有危险操作必须明确授权');
    await expect(page.getByText('记忆已保存，下一次模型调用会使用更新内容')).toBeVisible();
  });

  test('shows and removes structured Agent shared entities', async ({ page }) => {
    await openSettingsTab(page, '记忆');
    await expect(page.getByText('asset:comfyui:cover', { exact: true })).toBeVisible();
    await expect(page.getByText('generated/comfyui/cover.metadata.json', { exact: true })).toBeVisible();

    const patchRequest = page.waitForRequest((request) => (
      request.url().endsWith(`/memory/${sessionId}`)
      && request.method() === 'PATCH'
      && Array.isArray(request.postDataJSON().entities)
    ));
    await page.getByRole('button', { name: '删除共享实体 asset:comfyui:cover' }).click();
    const request = await patchRequest;
    expect(request.postDataJSON().entities).toEqual([]);
    await expect(page.getByText('共享实体 asset:comfyui:cover 已删除')).toBeVisible();
  });

  test('renders markdown blocks without injecting raw HTML', async ({ page }) => {
    await page.locator('[data-aether-composer]').fill('## 安全标题\n\n|项目|状态|\n|---|---|\n|授权|完成|\n\n<script>window.__unsafe = true</script>');
    await page.getByRole('button', { name: '发送' }).click();
    await expect(page.getByText('安全标题')).toBeVisible();
    await expect(page.getByRole('cell', { name: '完成' })).toBeVisible();
    expect(await page.evaluate(() => (window as typeof window & { __unsafe?: boolean }).__unsafe)).toBeUndefined();
  });

  test('uses the configured model list instead of hard-coded providers', async ({ page }) => {
    const targetSelect = page.getByRole('combobox', { name: '@全部模型' });
    await expect(targetSelect.locator('option')).toHaveCount(3);
    await expect(targetSelect.locator('option')).toHaveText(['@全部模型', '@deepseek', '@GLM']);
    await expect(targetSelect.locator('option', { hasText: '@claude' })).toHaveCount(0);
  });

  test('shows every task status including done', async ({ page }) => {
    await openSettingsTab(page, '任务');

    await expect(page.getByText('待办', { exact: true })).toBeVisible();
    await expect(page.getByText('已认领', { exact: true })).toBeVisible();
    await expect(page.getByText('进行中', { exact: true })).toBeVisible();
    await expect(page.getByText('评审', { exact: true })).toBeVisible();
    await expect(page.getByText('已合并', { exact: true })).toBeVisible();
    await expect(page.getByText('已完成', { exact: true })).toBeVisible();
    await expect(page.getByText('已完成任务', { exact: true })).toBeVisible();
  });

  test('creates a task with explicit dependencies', async ({ page }) => {
    await openSettingsTab(page, '任务');
    await page.getByLabel('前置依赖（可多选）').selectOption(['task-done', 'task-merged']);
    await page.getByPlaceholder('新建任务').fill('发布图片说明');
    const createRequest = page.waitForRequest((request) => (
      new URL(request.url()).pathname === '/tasks/'
      && request.method() === 'POST'
    ));
    await page.getByRole('button', { name: '添加', exact: true }).click();
    const request = await createRequest;
    expect(request.postDataJSON().depends_on).toEqual(['task-merged', 'task-done']);
  });

  test('creates and reviews durable read-only background schedules', async ({ page }) => {
    await openSettingsTab(page, '自动化');
    const dialog = page.getByLabel('自动化 panel');
    await expect(dialog.getByText('本机后台自动化', { exact: true })).toBeVisible();
    await expect(dialog.getByText(/无人值守任务默认只读/)).toBeVisible();
    await expect(dialog.getByText('每日项目摘要', { exact: true }).first()).toBeVisible();
    await expect(dialog.getByText('已完成只读项目检查，未执行任何副作用。')).toBeVisible();
    await expect(dialog.getByText('1 项待复核')).toBeVisible();

    await dialog.getByRole('button', { name: '标记已复核' }).click();
    await expect(dialog.getByText('0 项待复核')).toBeVisible();

    await dialog.getByPlaceholder('任务名称，例如：每日项目摘要').fill('每周风险摘要');
    await dialog.getByPlaceholder('后台执行指令').fill('只读汇总当前会话的风险和未完成事项。');
    const createRequest = page.waitForRequest((request) => (
      new URL(request.url()).pathname === '/automations/schedules'
      && request.method() === 'POST'
    ));
    await dialog.getByRole('button', { name: '创建只读定时任务' }).click();
    expect((await createRequest).postDataJSON().permission_mode).toBe('read_only');
    await expect(dialog.getByText('每周风险摘要', { exact: true })).toBeVisible();
    await expect(dialog.locator('article').filter({ hasText: '每日项目摘要' }).first().getByRole('button', { name: '立即运行' })).toBeVisible();
  });

  test('opens the command palette with Ctrl+K and navigates to a panel', async ({ page }) => {
    await page.keyboard.press('Control+K');
    const palette = page.getByRole('dialog', { name: '搜索命令、会话、任务、工件或模型' });
    await expect(palette).toBeVisible();
    const searchInput = palette.getByPlaceholder('输入关键词，例如：任务、README、模型');
    await expect(searchInput).toBeFocused();
    const focusStyle = await searchInput.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        outlineColor: style.outlineColor,
        boxShadow: style.boxShadow,
      };
    });
    expect(focusStyle.outlineColor).not.toBe('rgb(7, 94, 168)');
    expect(focusStyle.boxShadow).toBe('none');
    await expect.poll(() => searchInput.evaluate((element) => {
      const shell = element.closest<HTMLElement>('[data-aether-search-shell]');
      if (!shell) throw new Error('command search shell is missing');
      return getComputedStyle(shell).borderBottomColor;
    })).toBe('rgb(183, 154, 125)');
    await searchInput.fill('运行');
    await palette.getByRole('button', { name: /打开运行面板/ }).click();
    await expect(page.getByText('运行与沙箱')).toBeVisible();
  });

  test('does not call paid model verification when the run panel opens', async ({ page }) => {
    let verificationRequests = 0;
    page.on('request', (request) => {
      if (/\/models\/[^/]+\/verify$/.test(new URL(request.url()).pathname)) verificationRequests += 1;
    });

    await openSettingsTab(page, '运行');
    await expect(page.getByRole('button', { name: '基础检查' })).toBeVisible();
    await expect(page.getByRole('button', { name: '验证模型（可能计费）' })).toBeVisible();
    await expect(page.getByText('/ws/v1/sessions/:id', { exact: true })).toBeVisible();
    await page.waitForTimeout(250);
    expect(verificationRequests).toBe(0);
  });

  test('reports and verifies only models with usable credentials', async ({ page }) => {
    const verificationPaths: string[] = [];
    page.on('request', (request) => {
      const pathname = new URL(request.url()).pathname;
      if (/\/models\/[^/]+\/verify$/.test(pathname)) verificationPaths.push(pathname);
    });

    await openSettingsTab(page, '运行');
    const panel = page.getByLabel('运行 panel');
    await panel.getByRole('button', { name: '基础检查' }).click();
    await expect(panel.getByText('已配置 2/3 个模型，其中 1 个始终参与；基础检查不会调用模型 API')).toBeVisible();

    await panel.getByRole('button', { name: '验证模型（可能计费）' }).click();
    await acceptConfirmDialog(page);
    await expect(panel.getByText('已验证 1 个启用模型，通过 1 个')).toBeVisible();
    expect(verificationPaths).toEqual(['/models/deepseek/verify']);
  });

  test('selects a trusted Git repository and includes its verifier commands in a planned run', async ({ page }) => {
    await openSettingsTab(page, '运行');
    await page.getByRole('button', { name: '选择 Git 仓库' }).click();
    await expect(page.getByText('mock-git-repository', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Git 核验命令')).toHaveValue('python -m pytest -q');
    await page.keyboard.press('Escape');

    await page.locator('textarea[data-aether-composer]').fill('修改仓库并执行测试');
    await page.getByRole('button', { name: '发送' }).click();
    await expect.poll(async () => page.evaluate(() => (
      (window as typeof window & {
        __aetherWsActions?: Array<{
          type?: string;
          content?: string;
          repository_id?: string;
          verification_commands?: string[];
        }>;
      }).__aetherWsActions?.some((action) => (
        action.type === 'post_message'
        && action.content === '修改仓库并执行测试'
        && action.repository_id === 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        && action.verification_commands?.[0] === 'python -m pytest -q'
      ))
    ))).toBe(true);
  });

  test('reviews deterministic Git evidence before applying a candidate through the desktop bridge', async ({ page }) => {
    await openSettingsTab(page, '运行');
    const run = page.getByRole('article').filter({ hasText: '已完成代码候选' });
    await expect(run.getByText('候选 #1 · diff 与命令均通过')).toBeVisible();
    await expect(run.getByText('npm test · exit 0')).toBeVisible();
    await expect(run.getByText('M src/app.ts')).toBeVisible();
    await run.getByRole('button', { name: '审核并应用 diff' }).click();
    await expect(run.getByText('已应用（未 commit / push）')).toBeVisible();
    await expect.poll(async () => page.evaluate(() => (
      (window as typeof window & {
        __aetherDesktopActions?: Array<{ type?: string }>;
      }).__aetherDesktopActions?.some((action) => action.type === 'workspace-apply')
    ))).toBe(true);
  });

  test('shows recoverable agent steps and retries only unfinished work', async ({ page }) => {
    await openSettingsTab(page, '运行');
    const run = page.getByRole('article').filter({ hasText: '继续实现可恢复 Agent' });
    await expect(run.getByText('可恢复', { exact: true })).toBeVisible();
    await expect(run.getByText('工具 2 · 检查点 1 · 转向 1', { exact: true })).toBeVisible();
    await expect(run.getByText('执行 计划 · 来源 显式请求', { exact: true })).toBeVisible();
    await expect(run.getByText('副作用核验 0/1 · 存在未核验项', { exact: true })).toBeVisible();
    await expect(run.getByText('计划 2/5 · 核验 1', { exact: true })).toBeVisible();
    await expect(run.getByText('目标：完成可恢复 Agent 计划', { exact: true })).toBeVisible();
    await expect(run.getByText('核验结论：需修订', { exact: true })).toBeVisible();
    await expect(run.getByText('需要恢复 deepseek Worker 后再完成综合。')).toBeVisible();
    await expect(run.getByText('执行 · deepseek', { exact: true })).toBeVisible();
    await expect(run.getByText('已中断', { exact: true })).toBeVisible();
    await run.getByRole('button', { name: '重试未完成步骤' }).click();
    await expect.poll(async () => page.evaluate(() => (
      (window as typeof window & { __aetherWsActions?: Array<{ type?: string; run_id?: string }> }).__aetherWsActions
        ?.some((action) => action.type === 'retry_agent_run' && action.run_id === '88888888-8888-4888-8888-888888888888')
    ))).toBe(true);
  });

  test('opens the Agent collaboration audit log without exposing hidden reasoning', async ({ page }) => {
    await openSettingsTab(page, '运行');
    const run = page.getByRole('article').filter({ hasText: '继续实现可恢复 Agent' });
    await run.getByRole('button', { name: '协作日志' }).click();
    await expect(run.getByText('Agent 协作审计日志')).toBeVisible();
    await expect(run.getByText('不记录或暴露模型私密思维链')).toBeVisible();
    await expect(run.getByText('Used run_workflow')).toBeVisible();
    await expect(run.getByText('completed · 未核验', { exact: true })).toBeVisible();
    await expect(run.getByText('证据：tool_result_digest', { exact: true })).toBeVisible();
    await expect(run.getByText('tool_call', { exact: true })).toBeVisible();
    await expect(run.getByText(/已将工作流参数交给 ComfyUI/)).toBeVisible();
    await expect(run.getByText(/结果：/)).toContainText('cover');
  });

  test('isolates a broken run panel and exposes a diagnostic id', async ({ page }) => {
    await page.route('**/agent-runs?**', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{
        id: 'broken-run',
        session_id: sessionId,
        status: 'failed',
        mode: 'project',
        input: null,
        targets: [],
        retry_count: 0,
        created_at: '2026-08-11T00:00:00Z',
        updated_at: '2026-08-11T00:00:00Z',
        steps: [],
      }]),
    }));
    await page.reload();
    await expect(page.locator('textarea[data-aether-composer]')).toBeVisible();
    await openSettingsTab(page, '运行');

    await expect(page.getByText('运行面板暂时不可用')).toBeVisible();
    await expect(page.getByText(/^ui-[A-Za-z0-9-]+$/)).toBeVisible();
    await expect(page.getByRole('button', { name: '复制诊断码' })).toBeVisible();
    await expect(page.getByText('AetherChat 持久化工作区', { exact: true })).toBeVisible();
  });

  test('edits and confirms a persisted plan before workers start', async ({ page }) => {
    await openSettingsTab(page, '运行');
    const run = page.getByRole('article').filter({ hasText: '先确认部署计划' });
    await expect(run.getByText('等待计划确认', { exact: true })).toBeVisible();
    await expect(run.getByText('Worker 尚未启动；确认前不会产生后续模型调用。')).toBeVisible();
    await expect(run.getByText('计划版本 1 · 自动返工 0/1')).toBeVisible();

    await run.getByRole('button', { name: '编辑计划' }).click();
    await run.getByLabel('计划目标').fill('编辑后的部署目标');
    await run.getByLabel('计划修订说明').fill('E2E 审核后调整范围');
    await run.getByRole('button', { name: '保存计划草稿' }).click();

    await expect.poll(async () => page.evaluate(() => (
      (window as typeof window & {
        __aetherWsActions?: Array<{
          type?: string;
          run_id?: string;
          expected_revision?: number;
          goal_spec?: { objective?: string };
        }>;
      }).__aetherWsActions?.some((action) => (
        action.type === 'update_agent_plan'
        && action.run_id === '99999999-9999-4999-8999-999999999999'
        && action.expected_revision === 1
        && action.goal_spec?.objective === '编辑后的部署目标'
      ))
    ))).toBe(true);

    await expect(run.getByText('计划版本 2 · 自动返工 0/1')).toBeVisible();
    await expect(run.getByRole('button', { name: '编辑计划' })).toBeVisible();
    await run.getByRole('button', { name: '确认并执行' }).click();
    await expect.poll(async () => page.evaluate(() => (
      (window as typeof window & {
        __aetherWsActions?: Array<{
          type?: string;
          run_id?: string;
          expected_revision?: number;
        }>;
      }).__aetherWsActions?.some((action) => (
        action.type === 'confirm_agent_plan'
        && action.run_id === '99999999-9999-4999-8999-999999999999'
        && action.expected_revision === 2
      ))
    ))).toBe(true);
  });

  test('loads, toggles and invokes plugins from the plugin center', async ({ page }) => {
    await openSettingsTab(page, '插件');
    await expect(page.getByText('插件中心', { exact: true })).toBeVisible();
    await expect(page.getByText('应用 QA 诊断')).toBeVisible();
    await expect(page.getByText('浏览器链接')).toBeVisible();

    const qaCard = page.getByRole('article').filter({ hasText: '应用 QA 诊断' });
    await qaCard.getByRole('button', { name: '运行' }).click();
    await expect(page.getByText('最近结果 · 应用 QA 诊断')).toBeVisible();
    await expect(page.getByText('"healthy": true')).toBeVisible();

    await qaCard.getByRole('button', { name: '已启用' }).click();
    await expect(qaCard.getByRole('button', { name: '已停用' })).toBeVisible();
  });

  test('reviews MCP tools and changes model exposure permissions', async ({ page }) => {
    await openSettingsTab(page, '连接');
    await expect(page.getByRole('heading', { name: 'MCP 连接器' })).toBeVisible();
    const card = page.getByRole('article').filter({ hasText: '本地项目文件' });
    await expect(card.getByText('读取文件')).toBeVisible();
    const permissionRequest = page.waitForRequest((request) => request.url().endsWith('/mcp-connections/local-files') && request.method() === 'PATCH');
    await card.getByRole('checkbox').click();
    const request = await permissionRequest;
    expect(request.postDataJSON()).toEqual({ allowed_tools: [] });
    await expect(card.getByRole('checkbox')).not.toBeChecked();
    await expect(page.getByText('连接器权限已更新')).toBeVisible();
  });

  test('edits a selected local document, captures the screen and controls the mouse', async ({ page }) => {
    await openSettingsTab(page, '电脑');
    await expect(page.getByText('本地电脑', { exact: true })).toBeVisible();
    await expect(page.getByText('Computer Use 已连接')).toBeVisible();

    await page.getByRole('button', { name: '选择文档' }).click();
    const editor = page.getByLabel('本地文档编辑器');
    await expect(editor).toHaveValue('mock before');
    await editor.fill('mock after');
    const writeAuthorization = page.getByText('我确认本次写入；覆盖保存（包括另存为覆盖已有目标）会先创建时间戳备份');
    await writeAuthorization.click();
    await page.getByRole('button', { name: '覆盖保存' }).click();
    await expect(page.getByText('文档保存成功：mock-notes.md；已创建备份：mock-notes.aetherchat-backup.md')).toBeVisible();

    await writeAuthorization.click();
    await page.getByRole('button', { name: '另存为' }).click();
    await expect(page.getByText('已另存为 mock-notes-edited.md；已备份原文件：mock-notes-edited.aetherchat-backup.md')).toBeVisible();

    await page.getByRole('button', { name: '截取主屏幕' }).click();
    await expect(page.getByAltText('本机屏幕截图预览')).toBeVisible();
    await expect(page.getByText('观察屏幕', { exact: true })).toBeVisible();
    await expect(page.getByTestId('computer-action-timeline')).toContainText('观察完成');
    await page.getByRole('button', { name: '保存到“图片/Screenshots”' }).click();
    await expect(page.getByText('截图已保存：图片/Screenshots/AetherChat-test.png')).toBeVisible();
    await expect(page.locator('body')).not.toContainText('C:\\');

    await page.getByRole('button', { name: '读取位置' }).click();
    // Native cursor 50 maps to screenshot coordinate round(50 / 1.5) = 33.
    await expect(page.getByLabel('鼠标 X 坐标')).toHaveValue('33');
    await page.getByLabel('鼠标 X 坐标').fill('120');
    await page.getByLabel('鼠标 Y 坐标').fill('130');
    await page.getByText('我确认只授权下一次键盘或鼠标操作').click();
    await page.getByRole('button', { name: '移动鼠标' }).click();
    await expect(page.getByText('鼠标 移动完成')).toBeVisible();
    await expect(page.getByTestId('computer-action-timeline')).toContainText('截图已复核');

    await page.getByRole('button', { name: '会话授权' }).click();
    await expect(page.getByRole('button', { name: '会话授权' })).toHaveAttribute('aria-pressed', 'true');
    await page.getByLabel('Computer Use 输入文本').fill('hello from Computer Use');
    await page.getByRole('button', { name: '输入文本' }).click();
    await expect(page.getByTestId('computer-action-timeline')).toContainText('输入文本');

    await page.getByRole('button', { name: '立即接管' }).click();
    await expect(page.getByText(/你已接管电脑/)).toBeVisible();
    await expect(page.getByRole('button', { name: '逐次授权' })).toHaveAttribute('aria-pressed', 'true');
  });

  test('persists workspace settings and conversation selection without session data', async ({ page }) => {
    await page.getByRole('button', { name: '新建会话' }).click();
    const newConversation = page.getByRole('button', { name: /新协作会话/ }).first();
    await expect(newConversation).toHaveAttribute('aria-current', 'page');

    await page.getByTitle('设置').click();
    const languageSelect = page.getByText('语言策略', { exact: true }).locator('..').getByRole('combobox');
    await languageSelect.selectOption('中英双语');

    const persisted = await page.evaluate(() => JSON.parse(localStorage.getItem('aetherchat-workspace') || '{}'));
    expect(Object.keys(persisted.state).sort()).toEqual([
      'conversations',
      'selectedArtifactPath',
      'sessionId',
      'settings',
    ]);
    expect(persisted.state.settings.language).toBe('中英双语');
    expect(persisted.state.conversations[0].title).toBe('新协作会话');

    await page.reload();
    await expect(page.locator('textarea[data-aether-composer]')).toBeVisible();
    await expect(page.getByRole('button', { name: /新协作会话/ }).first()).toHaveAttribute('aria-current', 'page');
    await page.getByTitle('设置').click();
    await expect(page.getByText('语言策略', { exact: true }).locator('..').getByRole('combobox')).toHaveValue('中英双语');
  });

  test('exports a redacted diagnostic bundle from settings', async ({ page }) => {
    await page.getByTitle('设置').click();
    await expect(page.getByText(/哈希 trace 引用/)).toBeVisible();
    await expect(page.getByText(/原始 Run ID 或工具参数/)).toBeVisible();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出脱敏诊断包' }).click();
    expect((await download).suggestedFilename()).toBe('aetherchat-diagnostics-20260811T000000Z.zip');
    await expect(page.getByRole('status')).toContainText('不含请求正文、文件内容或凭据');
  });

  test('sends an interrupt action for the selected target', async ({ page }) => {
    const targetSelect = page.locator('select').first();
    await targetSelect.selectOption('GLM');
    await expect(page.getByRole('button', { name: /中断/ })).toHaveCount(0);
    await page.evaluate(() => (
      window as typeof window & { __emitAetherWs?: (payload: unknown) => void }
    ).__emitAetherWs?.({
      type: 'provider_status',
      model: 'GLM',
      phase: 'requesting',
      detail: '正在等待模型响应',
    }));
    await page.getByRole('button', { name: '中断 @GLM' }).click();

    await expect.poll(() => page.evaluate(() => (
      (window as typeof window & { __aetherWsActions?: Array<{ type?: string; scope?: string }> }).__aetherWsActions
        ?.some((action) => action.type === 'interrupt' && action.scope === 'GLM')
    ))).toBe(true);
  });

  test('downloads markdown and JSON exports from the run panel', async ({ page }) => {
    await openSettingsTab(page, '运行');

    const markdownDownload = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出 MD' }).click();
    expect((await markdownDownload).suggestedFilename()).toBe(`session_${sessionId}.md`);

    const jsonDownload = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出 JSON' }).click();
    expect((await jsonDownload).suggestedFilename()).toBe(`session_${sessionId}.json`);
  });

  test('previews, edits, compares and exports versioned artifacts safely', async ({ page }) => {
    await openSettingsTab(page, '文件');
    await expect(page.getByText('text/markdown · v2')).toBeVisible();
    await expect(page.getByTestId('artifact-preview').getByText('AetherChat', { exact: true })).toBeVisible();
    await expect(page.getByText('安全工件预览。')).toBeVisible();

    await page.getByRole('button', { name: '版本', exact: true }).click();
    await expect(page.getByText('v2 · 1234567', { exact: true })).toBeVisible();
    await expect(page.getByText('v1 · abcdef1', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '对比' }).click();
    await expect(page.getByTestId('artifact-diff')).toContainText('+安全工件预览。');

    await page.getByRole('button', { name: '编辑', exact: true }).click();
    await page.getByRole('textbox', { name: '工件内容' }).fill('# AetherChat\n\n新版本。');
    await page.getByRole('button', { name: '保存新版本' }).click();
    await expect(page.getByText('工件已加载')).toBeVisible();

    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'DOCX' }).click();
    expect((await download).suggestedFilename()).toBe('README.docx');
  });

  test('recovers a history-only artifact instead of leaving a stale 404', async ({ page }) => {
    await openSettingsTab(page, '文件');
    await page.getByRole('button', { name: /notes\/deleted\.md/ }).click();

    await expect(page.getByText('当前文件缺失 · 可从历史恢复')).toBeVisible();
    await expect(page.getByTestId('artifact-preview')).toContainText('可恢复的历史内容。');
    await page.getByRole('button', { name: '恢复 v1', exact: true }).click();
    await acceptConfirmDialog(page);

    await expect(page.getByText('工件已加载')).toBeVisible();
    await expect(page.getByRole('button', { name: '恢复 v1', exact: true })).toHaveCount(0);
  });

  test('switches the interface language without restart', async ({ page }) => {
    await page.getByRole('button', { name: '切换界面语言' }).click();
    await expect(page.getByRole('heading', { name: 'Unified conversation' })).toBeAttached();
    await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('lang', 'en-US');
    await expect(page.getByRole('button', { name: 'Send' })).toBeVisible();

    const panels = [
      ['Memory', 'Local session memory'],
      ['Knowledge', 'Project knowledge base'],
      ['Tasks', 'Add'],
      ['Automations', 'Local background automation'],
      ['Artifacts', 'File tree'],
      ['Changes', 'Changes and merges'],
      ['Tools', 'Tool catalog'],
      ['Plugins', 'Plugin center'],
      ['Connections', 'MCP connections'],
      ['Computer', 'Local computer'],
      ['Models', 'Model APIs'],
      ['Runs', 'Agent run history'],
      ['Settings', 'Collaboration settings'],
    ] as const;
    const untranslatedUi = /本机会话记忆|项目资料库|新建任务|本机后台自动化|创建定时任务|文件树|变更与合并|工具仓|插件中心|MCP 连接器|本地电脑|模型 API|Agent 运行记录|协作设置/;
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Back to chat' })).toBeVisible();
    await expect(page.getByRole('tablist', { name: 'Settings sections' })).toBeVisible();
    for (const [panel, expected] of panels) {
      await page.getByRole('tab', { name: panel, exact: true }).click();
      const pane = page.getByLabel(`${panel} panel`);
      await expect(pane).toContainText(expected);
      await expect(pane).not.toContainText(untranslatedUi);
    }

    await page.getByRole('button', { name: 'Switch interface language' }).click();
    await page.getByRole('button', { name: '返回对话' }).click();
    await expect(page.getByRole('heading', { name: '统一对话流' })).toBeAttached();
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  });

  test('traps and restores dialog focus and exposes a keyboard skip link', async ({ page }) => {
    await page.keyboard.press('Tab');
    const skipLink = page.getByRole('link', { name: /跳到主要内容/ });
    await expect(skipLink).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('#aether-main-content')).toBeFocused();

    const settingsButton = page.getByTitle('设置');
    await settingsButton.focus();
    await page.keyboard.press('Enter');
    const settingsPage = page.getByTestId('settings-page');
    await expect(settingsPage).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(settingsPage).toBeHidden();
  });

  test('has no serious or critical axe violations on the primary chat path', async ({ page }) => {
    const results = await new AxeBuilder({ page }).exclude('iframe').analyze();
    const severe = results.violations.filter((item) => item.impact === 'serious' || item.impact === 'critical');
    expect(severe, severe.map((item) => `${item.id}: ${item.help}`).join('\n')).toEqual([]);
  });

  test('supports reduced motion and a narrow viewport equivalent to 200 percent zoom', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const client = await page.context().newCDPSession(page);
    await client.send('Emulation.setDeviceMetricsOverride', {
      width: 720,
      height: 450,
      screenWidth: 1440,
      screenHeight: 900,
      deviceScaleFactor: 2,
      mobile: false,
    });
    await page.reload();
    await expect(page.locator('textarea[data-aether-composer]')).toBeVisible();
    await expect.poll(() => page.evaluate(() => ({ width: innerWidth, height: innerHeight, ratio: devicePixelRatio })))
      .toEqual({ width: 720, height: 450, ratio: 2 });
    const transition = await page.getByRole('button', { name: '发送' }).evaluate((element) => getComputedStyle(element).transitionDuration);
    expect(transition).not.toBe('0.12s');
    await expectNoHorizontalPageOverflow(page);

    await page.getByTitle('设置').click();
    const dialog = page.getByTestId('settings-page');
    const bounds = await dialog.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(720);
    await expect(dialog.getByRole('button', { name: '返回对话' })).toBeVisible();
    expect(await dialog.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    await expectNoHorizontalPageOverflow(page);
  });

  test('keeps focus and selected controls visible in forced-colors mode', async ({ page }) => {
    await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
    await page.reload();
    await expect(page.locator('textarea[data-aether-composer]')).toBeVisible();
    expect(await page.evaluate(() => matchMedia('(forced-colors: active)').matches)).toBe(true);

    await page.keyboard.press('Tab');
    const skipLink = page.getByRole('link', { name: '跳到主要内容' });
    await expect(skipLink).toBeFocused();
    const focusStyle = await skipLink.evaluate((element) => {
      const style = getComputedStyle(element);
      return { outlineStyle: style.outlineStyle, outlineWidth: Number.parseFloat(style.outlineWidth) };
    });
    expect(focusStyle.outlineStyle).not.toBe('none');
    expect(focusStyle.outlineWidth).toBeGreaterThanOrEqual(3);

    await page.getByTitle('设置').click();
    const selectedTab = page.getByRole('tab', { name: '设置', exact: true });
    await expect(selectedTab).toHaveAttribute('aria-selected', 'true');
    const selectedStyle = await selectedTab.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        backgroundColor: style.backgroundColor,
        color: style.color,
        borderStyle: style.borderStyle,
        forcedColorAdjust: style.forcedColorAdjust,
      };
    });
    expect(selectedStyle.backgroundColor).not.toBe(selectedStyle.color);
    expect(selectedStyle.borderStyle).not.toBe('none');
    expect(selectedStyle.forcedColorAdjust).toBe('auto');

    const results = await new AxeBuilder({ page }).exclude('iframe').analyze();
    const severe = results.violations.filter((item) => item.impact === 'serious' || item.impact === 'critical');
    expect(severe, severe.map((item) => `${item.id}: ${item.help}`).join('\n')).toEqual([]);
  });

  test('fills empty-state examples into the composer', async ({ page }) => {
    // Clear messages so empty state is visible.
    await page.route(/^http:\/\/(localhost|127\.0\.0\.1):8000\/sessions\/[^/]+\/messages/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: '[]',
      });
    });
    await page.reload();
    const example = page.locator('button').filter({ hasText: '快速认识工作台' }).first();
    await expect(example).toBeVisible();
    await expect(example).toBeEnabled();
    await example.click();
    await expect(page.locator('textarea[data-aether-composer]')).toHaveValue(
      /用三句话介绍你能帮我做什么/,
    );
  });
});

test.describe('AetherChat progressive disclosure', () => {
  test('keeps primary panels visible and reveals advanced panels on demand', async ({ page }) => {
    page.on('pageerror', (error) => console.error(`Browser page error: ${error.message}`));
    await installWorkspaceUiDefaults(page, { expandAdvancedRail: false });
    await installMockWebSocket(page);
    await installMockDesktopBridge(page);
    await installMockApi(page);
    await page.goto('/');
    await expect(page.locator('textarea[data-aether-composer]')).toBeVisible();

    await expect(page.getByRole('button', { name: '设置', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '模型', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '更多面板' })).toHaveCount(0);

    await page.getByRole('button', { name: '设置', exact: true }).click();
    await expect(page.getByTestId('settings-page')).toBeVisible();
    await expect(page.getByRole('tab', { name: '模型', exact: true })).toBeVisible();
    await expect(page.getByRole('tab', { name: '运行', exact: true })).toBeVisible();
    await expect(page.getByRole('tab', { name: '记忆', exact: true })).toBeVisible();
    await page.getByRole('tab', { name: '记忆', exact: true }).click();
    await expect(page.getByRole('heading', { name: '本机会话记忆' })).toBeVisible();
  });
});

test.describe('AetherChat first-run wizard', () => {
  test('walks cloud preset save and verify without leaving the main path blocked', async ({ page }) => {
    page.on('pageerror', (error) => console.error(`Browser page error: ${error.message}`));
    await installWorkspaceUiDefaults(page, { dismissFirstRun: false, expandAdvancedRail: true });
    await installMockWebSocket(page);
    await installMockDesktopBridge(page);

    // No configured models → wizard should open after hydrate.
    const unconfigured = [{
      name: 'claude',
      type: 'anthropic',
      model: 'claude-3-5-sonnet',
      base_url: '',
      api_key_ref: 'ANTHROPIC_API_KEY',
      has_api_key: false,
      max_tokens: 8192,
      participation_policy: 'always',
      persona: '架构评审',
      token_limit: 128000,
      created_at: '2026-01-01T00:00:00.000Z',
      updated_at: '2026-01-01T00:00:00.000Z',
    }];
    await page.route(/^http:\/\/(localhost|127\.0\.0\.1):8000\/.*/, async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const { pathname } = url;
      const json = (body: unknown, status = 200) => route.fulfill({
        status,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify(body),
      });
      if (pathname === '/health') return json({ status: 'ok', service: 'aetherchat', redis: false });
      if (pathname === '/health/ready') {
        return json({
          status: 'ready', ready: true, service: 'aetherchat',
          checks: { database: { status: 'ready', schema_version: 4, expected_schema_version: 4 } },
          optional_dependencies: { redis: { status: 'unavailable', required: false } },
        });
      }
      if (pathname === '/auth/token') return json({ access_token: 'mock-token', token_type: 'bearer' });
      if (pathname === '/sessions/' && request.method() === 'GET') {
        return json([{
          id: sessionId, title: '向导会话', summary: '', mode: 'project',
          created_at: '2026-01-01T00:00:00.000Z', updated_at: '2026-01-01T00:00:00.000Z',
          message_count: 0, task_count: 0, open_task_count: 0,
        }]);
      }
      if (pathname === '/models/' && request.method() === 'GET') return json(unconfigured);
      if (pathname === '/models/' && request.method() === 'POST') {
        const payload = request.postDataJSON();
        return json({
          ...unconfigured[0],
          name: payload.name || 'kimi',
          type: payload.type || 'openai_compat',
          model: payload.model || 'kimi-k3',
          base_url: payload.base_url || 'https://api.moonshot.cn/v1',
          has_api_key: true,
        }, 201);
      }
      if (/^\/models\/[^/]+\/verify$/.test(pathname) && request.method() === 'POST') {
        return json({ ok: true, message: '真实对话检测通过', sample: '向导OK' });
      }
      if (pathname.startsWith('/sessions/')) return json([]);
      if (pathname.startsWith('/agent-runs')) return json([]);
      if (pathname.startsWith('/tasks')) return json([]);
      if (pathname.startsWith('/tools')) return json([]);
      if (pathname.startsWith('/artifacts')) return json([]);
      if (pathname.startsWith('/memory')) return json({
        session_id: sessionId, summary: '', project_note: '', decisions: [], preferences: [],
        open_items: [], entities: [], message_count: 0, updated_at: '2026-01-01T00:00:00.000Z',
      });
      if (pathname.startsWith('/automations')) return json([]);
      if (pathname.startsWith('/mcp')) return json([]);
      if (pathname.startsWith('/plugins')) return json([]);
      if (pathname.startsWith('/workspaces')) return json([]);
      return json({});
    });

    await page.goto('/');
    const wizard = page.getByRole('dialog', { name: /欢迎使用 AetherChat|配置一个可对话的模型|已就绪/ });
    await expect(wizard).toBeVisible();
    await expect(page.getByRole('heading', { name: '欢迎使用 AetherChat' })).toBeVisible();
    await page.getByRole('button', { name: '开始配置' }).click();
    await expect(page.getByRole('heading', { name: '配置一个可对话的模型' })).toBeVisible();
    await page.getByPlaceholder('sk-...').fill('sk-e2e-test-key');
    await page.getByRole('button', { name: '保存并验证' }).click();
    await expect(page.getByRole('heading', { name: '已就绪，可以开始' })).toBeVisible();
    await expect(page.getByText(/样本回复：向导OK/)).toBeVisible();
    await page.getByRole('button', { name: '进入工作台' }).click();
    await expect(page.getByRole('dialog', { name: /欢迎使用 AetherChat|配置一个可对话的模型|已就绪/ })).toHaveCount(0);
    await expect(page.locator('textarea[data-aether-composer]')).toBeVisible();
  });

  test('has no serious or critical axe violations on the first-run wizard', async ({ page }) => {
    await installWorkspaceUiDefaults(page, { dismissFirstRun: false });
    await installMockWebSocket(page);
    await installMockApi(page);
    // Force unconfigured models for wizard.
    await page.route(/^http:\/\/(localhost|127\.0\.0\.1):8000\/models\/$/, async (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify([{
          name: 'claude', type: 'anthropic', model: 'claude-3-5-sonnet', base_url: '',
          api_key_ref: 'ANTHROPIC_API_KEY', has_api_key: false, max_tokens: 8192,
          participation_policy: 'always', persona: '架构评审', token_limit: 128000,
          created_at: '2026-01-01T00:00:00.000Z', updated_at: '2026-01-01T00:00:00.000Z',
        }]),
      });
    });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: '欢迎使用 AetherChat' })).toBeVisible();
    const results = await new AxeBuilder({ page }).exclude('iframe').analyze();
    const severe = results.violations.filter((item) => item.impact === 'serious' || item.impact === 'critical');
    expect(severe, severe.map((item) => `${item.id}: ${item.help}`).join('\n')).toEqual([]);
  });
});

test.describe('AetherChat desktop startup recovery', () => {
  test('blocks the workspace and gives safe schema mismatch recovery guidance', async ({ page }) => {
    await installWorkspaceUiDefaults(page);
    await page.addInitScript(() => {
      Object.defineProperty(window, 'aetherRuntime', {
        configurable: true,
        value: {
          apiBase: 'http://127.0.0.1:8000',
          backendStatus: 'unavailable',
          backendErrorCode: 'database_schema_mismatch',
          backendRequestId: 'desktop-schema-e2e',
        },
      });
    });
    await page.goto('/');

    const alert = page.getByRole('alert');
    await expect(alert).toContainText('本机数据库版本不兼容');
    await expect(alert).toContainText('请不要删除用户数据');
    await expect(alert).toContainText('database_schema_mismatch');
    await expect(alert).toContainText('desktop-schema-e2e');
    await expect(page.getByText('统一对话流')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '重试启动' })).toBeVisible();
  });
});

test.describe('AetherChat offline behavior', () => {
  test('keeps an unsent message in the composer when WebSocket is unavailable', async ({ page }) => {
    await installWorkspaceUiDefaults(page);
    await installMockWebSocket(page, false);
    await installMockApi(page);
    await page.goto('/');

    const composer = page.locator('textarea[data-aether-composer]');
    await composer.fill('这条消息必须保留');
    await page.getByRole('button', { name: '发送' }).click();

    await expect(composer).toHaveValue('这条消息必须保留');
    await expect(page.getByText('后端未连接，消息未发送。内容仍保留在输入框中，请等待连接恢复后重试。')).toBeVisible();
    expect(await page.evaluate(() => (
      (window as typeof window & { __aetherWsActions?: unknown[] }).__aetherWsActions?.length
    ))).toBe(0);
  });
});

test.describe('AetherChat viewport and zoom baseline', () => {
  for (const viewport of [
    { name: '960x540', width: 960, height: 540 },
    { name: '1280x720', width: 1280, height: 720 },
    { name: '720x450-200pct', width: 720, height: 450 },
  ] as const) {
    test(`primary chrome stays usable at ${viewport.name}`, async ({ page }) => {
      await installWorkspaceUiDefaults(page);
      await installMockWebSocket(page);
      await installMockDesktopBridge(page);
      await installMockApi(page);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto('/');
      await expect(page.locator('textarea[data-aether-composer]')).toBeVisible();
      await expect(page.getByTitle('设置')).toBeVisible();
      await expect(page.getByRole('button', { name: '发送' })).toBeVisible();
      await expectNoHorizontalPageOverflow(page);
      await page.getByTitle('设置').click();
      const dialog = page.getByTestId('settings-page');
      await expect(dialog).toBeVisible();
      const bounds = await dialog.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width + 1);
      expect(await dialog.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
      await expectNoHorizontalPageOverflow(page);
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
    });
  }
});
