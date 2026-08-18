import type { WorkspaceSettings } from '../types';

export const SETTING_TO_MODE: Record<WorkspaceSettings['mode'], 'casual' | 'roundtable' | 'project'> = {
  '日常讨论': 'casual',
  '圆桌评审': 'roundtable',
  '项目协作': 'project',
};

export const MODE_TO_SETTING: Record<'casual' | 'roundtable' | 'project', WorkspaceSettings['mode']> = {
  casual: '日常讨论',
  roundtable: '圆桌评审',
  project: '项目协作',
};
