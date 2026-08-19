import type { ComputerAction, ComputerPermissionMode } from '../types/desktop';

export type TimelineItem = {
  id: string;
  action: ComputerAction;
  label: string;
  phase: 'running' | 'verified' | 'failed' | 'interrupted';
  at: string;
  durationMs?: number;
  detail?: string;
};

export const ACTION_LABELS: Record<ComputerAction, string> = {
  screenshot: '观察屏幕',
  mouse_move: '移动鼠标',
  left_click: '左键单击',
  right_click: '右键单击',
  middle_click: '中键单击',
  double_click: '双击',
  triple_click: '三击',
  left_click_drag: '拖拽',
  left_mouse_down: '按住左键',
  left_mouse_up: '释放左键',
  scroll: '滚动',
  type: '输入文本',
  key: '发送按键',
  hold_key: '保持按键',
  wait: '等待界面',
  zoom: '区域放大',
  cursor_position: '读取光标',
};

export const PERMISSION_MODES: Array<{
  mode: ComputerPermissionMode;
  label: string;
  description: string;
}> = [
  { mode: 'manual', label: '逐次授权', description: '每次操作前确认' },
  { mode: 'observe', label: '仅观察', description: '禁止键鼠输入' },
  { mode: 'auto', label: '会话授权', description: '本次会话自动执行' },
];

export const timelineTimestamp = () => new Date().toISOString();
