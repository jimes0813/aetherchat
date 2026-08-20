import { beforeEach, describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { localize, translate, useI18n, type TranslationKey } from './i18n';
import { useSessionStore } from './stores/sessionStore';

const initialState = useSessionStore.getState();

beforeEach(() => {
  window.localStorage.clear();
  useSessionStore.setState(initialState, true);
});

describe('i18n: translate() 查表', () => {
  it('zh-CN 命中返回中文词条', () => {
    expect(translate('zh-CN', 'send')).toBe('发送');
    expect(translate('zh-CN', 'chat')).toBe('对话');
    expect(translate('zh-CN', 'settings')).toBe('设置');
  });

  it('en-US 命中返回英文词条', () => {
    expect(translate('en-US', 'send')).toBe('Send');
    expect(translate('en-US', 'chat')).toBe('Chat');
    expect(translate('en-US', 'feedTitle')).toBe('Unified conversation');
  });

  it('按 {name} 占位符插值', () => {
    expect(translate('zh-CN', 'streaming', { model: 'claude' })).toBe('claude 正在输出');
    expect(translate('en-US', 'streaming', { model: 'claude' })).toBe('claude is responding');
    expect(translate('en-US', 'chunks', { count: 3 })).toBe('3 chunks');
  });

  it('同一占位符出现多次时全部替换', () => {
    expect(translate('zh-CN', 'approvalRequest', { model: 'gpt', tool: 'shell' }))
      .toBe('gpt · shell');
  });

  it('未知 key 回退到 key 本身', () => {
    expect(translate('zh-CN', 'nonexistent-key' as TranslationKey)).toBe('nonexistent-key');
    expect(translate('en-US', 'nonexistent-key' as TranslationKey)).toBe('nonexistent-key');
  });
});

describe('i18n: en-US 词条表关键条目存在性', () => {
  const keyEntries: Array<[TranslationKey, string]> = [
    ['chat', 'Chat'],
    ['memory', 'Memory'],
    ['tasks', 'Tasks'],
    ['artifacts', 'Artifacts'],
    ['tools', 'Tools'],
    ['models', 'Models'],
    ['settings', 'Settings'],
    ['send', 'Send'],
    ['you', 'You'],
    ['allModels', '@All models'],
    ['approvalLabel', 'Model action authorization'],
    ['approvalPromptHeading', 'Allow {tool}?'],
    ['deny', 'Deny'],
    ['allowOnce', 'Allow once'],
    ['allowSession', 'Allow for session'],
    ['close', 'Close'],
    ['uiLanguage', 'Interface language'],
    ['currentChat', 'Current chat'],
    ['chatHistory', 'Chat history'],
    ['taskRecords', 'Task records'],
    ['chatAndModels', 'Chat and models'],
    ['knowledgeAndMemory', 'Knowledge and memory'],
    ['agentsAndAutomation', 'Agents and automation'],
    ['advanced', 'Advanced'],
    ['general', 'General'],
    ['personalization', 'Personalization'],
    ['computerUse', 'Computer Use'],
    ['files', 'Files'],
    ['integrations', 'Integrations'],
    ['backToChat', 'Back to chat'],
    ['settingsGroups', 'Settings sections'],
    ['inspector', 'Inspector'],
    ['sessionMemoryTitle', 'Local session memory'],
    ['toolCatalog', 'Tool catalog'],
    ['backendApi', 'Backend API'],
    ['modelConnectionCheck', 'Model connection check'],
    ['skipToMain', 'Skip to main content'],
    ['newChat', 'New chat'],
    ['sessionAllowance', 'Session allowance'],
    ['connected', 'Connected'],
    ['commandPaletteLabel', 'Search commands, conversations, tasks, artifacts, or models'],
    ['chatHistory', 'Chat history'],
    ['currentTasks', 'Current tasks'],
    ['openPanel', 'Open the {panel} panel'],
    ['startVoiceChat', 'Start voice conversation'],
  ];

  it.each(keyEntries)('en-US 词条 %s 存在且正确', (key, expected) => {
    expect(translate('en-US', key)).toBe(expected);
  });

  it('en-US 与 zh-CN 词条集合形状一致（关键键两侧都有）', () => {
    const probeKeys: TranslationKey[] = [
      'send', 'interrupt', 'streaming', 'approvalRequest', 'approvalRisk',
      'approvalDenyChoice', 'approvalAllowOnceChoice', 'approvalAllowSessionChoice',
      'budget', 'sandbox', 'switchUiLanguage', 'live', 'emptyTitle', 'emptyDescription',
    ];
    for (const key of probeKeys) {
      expect(translate('zh-CN', key)).not.toBe(key);
      expect(translate('en-US', key)).not.toBe(key);
    }
  });
});

describe('i18n: localize()（ui() 底层）', () => {
  it('zh-CN 原样返回中文文本', () => {
    expect(localize('zh-CN', '新建会话')).toBe('新建会话');
  });

  it('en-US 命中英文表时返回译文', () => {
    expect(localize('en-US', '新建会话')).toBe('New conversation');
    expect(localize('en-US', '历史聊天')).toBe('Chat history');
    expect(localize('en-US', '已连接')).toBe('Connected');
    expect(localize('en-US', '离线')).toBe('Offline');
  });

  it('en-US 未命中时回退为原始中文文本', () => {
    expect(localize('en-US', '某句没有译文的话')).toBe('某句没有译文的话');
  });

  it('en-US 命中后支持占位符插值', () => {
    expect(localize('en-US', '{count} 个会话', { count: 5 })).toBe('5 conversations');
    expect(localize('en-US', '每 {count} 分钟', { count: 10 })).toBe('Every 10 minutes');
  });

  it('zh-CN 下也可对中文原文插值', () => {
    expect(localize('zh-CN', '{count} 条消息', { count: 2 })).toBe('2 条消息');
  });

  it('en-US 前缀规则翻译动态错误消息', () => {
    expect(localize('en-US', '保存失败：磁盘已满')).toBe('Save failed: 磁盘已满');
    expect(localize('en-US', '正在连接 本地服务')).toBe('Connecting 本地服务');
  });
});

describe('i18n: useI18n() hook', () => {
  it('默认语言为 zh-CN，ui() 原样返回', () => {
    const { result } = renderHook(() => useI18n());
    expect(result.current.locale).toBe('zh-CN');
    expect(result.current.t('send')).toBe('发送');
    expect(result.current.ui('新建会话')).toBe('新建会话');
  });

  it('切换 uiLocale 为 en-US 后 t()/ui() 走英文表', () => {
    const { result } = renderHook(() => useI18n());
    act(() => {
      useSessionStore.getState().updateSettings({ uiLocale: 'en-US' });
    });

    expect(result.current.locale).toBe('en-US');
    expect(result.current.t('send')).toBe('Send');
    expect(result.current.ui('新建会话')).toBe('New conversation');
    // 未命中英文表时回退中文原文
    expect(result.current.ui('没有对应翻译的文本')).toBe('没有对应翻译的文本');
  });
});
