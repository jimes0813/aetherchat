import type {
  DesktopDocumentSaveResult,
  DesktopMouseResult,
  DesktopScreenshotSaveResult,
  ExternalNavigationReceipt,
  SelectedDesktopDocument,
} from '../components/shared';
import type { AgentWorkspaceCandidate, GitRepository } from './index';

export type ComputerAction =
  | 'screenshot'
  | 'mouse_move'
  | 'left_click'
  | 'right_click'
  | 'middle_click'
  | 'double_click'
  | 'triple_click'
  | 'left_click_drag'
  | 'left_mouse_down'
  | 'left_mouse_up'
  | 'scroll'
  | 'type'
  | 'key'
  | 'hold_key'
  | 'wait'
  | 'zoom'
  | 'cursor_position';

export type ComputerPermissionMode = 'manual' | 'observe' | 'auto';

export type ComputerActionOptions = {
  coordinate?: [number, number];
  start_coordinate?: [number, number];
  region?: [number, number, number, number];
  text?: string;
  duration?: number;
  scroll_direction?: 'up' | 'down' | 'left' | 'right';
  scroll_amount?: number;
  delta_x?: number;
  delta_y?: number;
  modifiers?: string[];
};

export type ComputerScreenshot = {
  image_data_url: string;
  width: number;
  height: number;
  display_width_px: number;
  display_height_px: number;
  native_width: number;
  native_height: number;
  origin_x: number;
  origin_y: number;
  scale_x: number;
  scale_y: number;
};

export type ComputerActionReceipt = {
  protocol: 'computer_20251124';
  action_id: string;
  action: ComputerAction;
  status: 'success';
  started_at: string;
  completed_at: string;
  duration_ms: number;
  result: Record<string, unknown>;
  cursor_before: { x: number; y: number };
  cursor_after: { x: number; y: number };
  verification: 'post_action_screenshot' | 'observation';
  screenshot?: ComputerScreenshot;
};

export type ComputerEvent = {
  type: 'action_started' | 'action_completed' | 'action_failed' | 'permission_changed' | 'interrupted';
  action?: ComputerAction;
  actionId?: string;
  durationMs?: number;
  permissionMode?: ComputerPermissionMode;
  error?: string;
  at: string;
};

export type ComputerHistoryEvent = {
  sequence: number;
  action_id: string;
  action: ComputerAction;
  phase: 'running' | 'verified' | 'failed' | 'interrupted';
  at: string;
  duration_ms?: number;
  detail?: string;
};

export type ComputerHistoryResponse = {
  events: ComputerHistoryEvent[];
  latest_sequence: number;
  active_action_id: string | null;
  latest_receipt: ComputerActionReceipt | null;
};

export type LaunchIntent = {
  id: string;
  action: 'compose';
  prompt: string;
  mode: 'casual' | 'roundtable' | 'project' | null;
  newConversation: boolean;
  source: 'protocol' | 'shortcut';
};

declare global {
  interface Window {
    aetherWindow?: {
      minimize: () => void;
      toggleMaximize: () => void;
      close: () => void;
    };
    aetherRuntime?: {
      apiBase: string;
      backendStatus: 'starting' | 'ready' | 'unavailable';
      backendErrorCode?: string;
      backendRequestId?: string;
      appVersion?: string;
      platform?: string;
      quickEntryShortcut?: string;
      chromiumSandbox?: 'on' | 'off' | string;
      installChannel?: 'development' | 'system-deb' | 'user-appimage' | 'user-unpacked' | 'windows-nsis' | 'packaged' | string;
      installPath?: string;
      authIntercept?: boolean;
      getApiToken?: () => Promise<string>;
      openExternal?: (
        url: string,
        options?: { browser?: 'default' | 'firefox' },
      ) => Promise<ExternalNavigationReceipt>;
      openArtifact?: (
        sessionId: string,
        path: string,
        action?: 'open' | 'reveal',
      ) => Promise<{ opened: boolean; action: 'open' | 'reveal'; displayName: string }>;
      onLaunchIntent?: (callback: (intent: LaunchIntent) => void) => () => void;
      onBackgroundChange?: (
        callback: (payload: { reviewItems: number }) => void,
      ) => () => void;
    };
    aetherDesktop?: {
      status: () => Promise<Record<string, unknown>>;
      selectDocument: () => Promise<SelectedDesktopDocument | null>;
      saveDocument: (handle: string, data: Record<string, unknown>) => Promise<DesktopDocumentSaveResult>;
      saveDocumentAs: (handle: string, data: Record<string, unknown>) => Promise<(DesktopDocumentSaveResult & { handle: string }) | null>;
      captureScreen: () => Promise<{ dataUrl: string; width: number; height: number; capturedAt: string }>;
      saveScreenshot: (dataUrl: string) => Promise<DesktopScreenshotSaveResult>;
      mouse: (action: 'get' | 'move' | 'click', options?: { x?: number; y?: number; button?: 'left' | 'right' | 'middle' }) => Promise<DesktopMouseResult>;
      computerAction: (action: ComputerAction, options?: ComputerActionOptions) => Promise<ComputerActionReceipt>;
      setComputerPermission: (mode: ComputerPermissionMode) => Promise<{ permissionMode: ComputerPermissionMode }>;
      cancelComputer: () => Promise<{ cancelled: boolean; action_id?: string | null; permissionMode: ComputerPermissionMode }>;
      computerHistory: (after?: number) => Promise<ComputerHistoryResponse>;
      onComputerEvent: (callback: (event: ComputerEvent) => void) => () => void;
    };
    aetherWorkspace?: {
      selectRepository: (sessionId: string) => Promise<GitRepository | null>;
      decideCandidate: (
        workspaceId: string,
        sessionId: string,
        decision: 'apply' | 'reject',
        summary?: { changedFiles?: number; verificationStatus?: string },
      ) => Promise<AgentWorkspaceCandidate | null>;
    };
  }
}

export {};
