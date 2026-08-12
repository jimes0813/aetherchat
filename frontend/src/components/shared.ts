import type { ModelConfig, Task } from '../types';

export const API_BASE = window.aetherRuntime?.apiBase ?? import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';

export const isConfiguredModel = (model: ModelConfig) => model.type === 'local' || model.has_api_key;

export const formatDateTime = (value?: string | null) => {
  if (!value) return '尚未开始';
  return new Intl.DateTimeFormat(document.documentElement.lang === 'en-US' ? 'en-US' : 'zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value));
};

export type DesktopDocumentSnapshot = {
  name: string;
  format: string;
  size_bytes: number;
  modified_at: string;
  editor_mode: 'text' | 'structured' | 'pdf_note';
  data: Record<string, unknown>;
  read_only_text?: string;
  page_count?: number;
  truncated?: boolean;
};

export type SelectedDesktopDocument = {
  handle: string;
  displayName: string;
  document: DesktopDocumentSnapshot;
};

export type DesktopMouseResult = {
  action: 'get' | 'move' | 'click';
  position: { x: number; y: number };
  button?: string | null;
  virtual_desktop: { left: number; top: number; width: number; height: number };
};

export type DesktopDocumentSaveResult = {
  saved: boolean;
  displayName: string;
  sizeBytes: number;
  savedAt: string;
  backupCreated: boolean;
  backupDisplayName?: string;
};

export type DesktopScreenshotSaveResult = {
  saved: boolean;
  displayName: string;
  locationLabel: string;
  sizeBytes: number;
};

export type ExternalNavigationReceipt = {
  success: true;
  kind: 'external_navigation';
  url: string;
  browser: 'default' | 'firefox';
  launcher: 'system_default' | 'firefox';
  verification: 'launcher_accepted';
  accepted_at: string;
};

export const taskStatusLabel: Record<Task['status'], string> = {
  backlog: '待处理', claimed: '已认领', in_progress: '进行中', review: '评审中', merged: '已合并', done: '已完成',
};
