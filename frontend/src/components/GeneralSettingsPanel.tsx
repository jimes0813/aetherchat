import { Archive, ShieldCheck } from 'lucide-react';
import { useI18n } from '../i18n';
import type { Provider, WorkspaceSettings } from '../types';

type Props = {
  settings: WorkspaceSettings;
  providers: Provider[];
  diagnosticStatus: string;
  onUpdate: (settings: Partial<WorkspaceSettings>) => void;
  onDownloadDiagnostics: () => void;
};

const SELECTS: Array<[keyof WorkspaceSettings, string, string[]]> = [
  ['mode', '协作模式', ['日常讨论', '圆桌评审', '项目协作']],
  ['mergeRule', '合并规则', ['两票通过', '主持人通过', '人工确认']],
  ['sandbox', '沙箱策略', ['Docker 严格沙箱', '只读预览', '关闭执行']],
  ['language', '语言策略', ['中文优先', '中英双语']],
  ['uiLocale', '界面语言', ['zh-CN', 'en-US']],
  ['reasoningDisplay', '思考过程摘要', ['显示', '隐藏']],
];

export function GeneralSettingsPanel({ settings, providers, diagnosticStatus, onUpdate, onDownloadDiagnostics }: Props) {
  const { t, ui } = useI18n();
  return (
    <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4" data-settings-panel="settings">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold"><ShieldCheck size={16} />{t('general')}</div>

      <label className="mb-4 block">
        <span className="mb-1 block text-xs font-semibold text-ink-muted">{t('workspaceMode')}</span>
        <select
          aria-label={t('workspaceMode')}
          value={settings.workspaceMode || 'simple'}
          onChange={(event) => onUpdate({ workspaceMode: event.target.value as WorkspaceSettings['workspaceMode'] })}
          className="h-10 w-full rounded-control border border-border bg-paper-raised px-3 text-sm outline-none"
        >
          <option value="simple">{t('workspaceModeSimple')}</option>
          <option value="advanced">{t('workspaceModeAdvanced')}</option>
        </select>
        <span className="mt-1 block text-xs leading-5 text-ink-muted">{t('workspaceModeDescription')}</span>
      </label>
      {SELECTS.map(([key, label, options]) => (
        <label key={String(key)} className="mb-4 block">
          <span className="mb-1 block text-xs font-semibold text-[#7a7065]">{ui(label)}</span>
          <select
            aria-label={ui(label)}
            value={String(settings[key] ?? (key === 'reasoningDisplay' ? '显示' : ''))}
            onChange={(event) => onUpdate({ [key]: event.target.value } as Partial<WorkspaceSettings>)}
            className="h-10 w-full rounded-control border border-border bg-paper-raised px-3 text-sm outline-none"
          >
            {options.map((option) => <option key={option} value={option}>{option === 'zh-CN' ? t('simplifiedChinese') : option === 'en-US' ? 'English' : ui(option)}</option>)}
          </select>
        </label>
      ))}
      <label className="mb-5 flex items-start gap-3 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3">
        <input type="checkbox" checked={Boolean(settings.developerMode)} onChange={(event) => onUpdate({ developerMode: event.target.checked })} className="mt-0.5 h-4 w-4" />
        <span><span className="block text-xs font-semibold text-[#574a3e]">{t('developerMode')}</span><span className="mt-1 block text-xs leading-5 text-[#7a7065]">{t('developerModeDescription')}</span></span>
      </label>
      <section className="mb-5 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3">
        <div className="flex items-start gap-2"><Archive size={16} className="mt-0.5 shrink-0 text-[#6b523c]" /><div><div className="text-xs font-semibold text-[#574a3e]">{t('redactedDiagnosticBundle')}</div><p className="mt-1 text-xs leading-5 text-[#7a7065]">{t('exportSchemaRunMetricsHashed')}</p></div></div>
        <button type="button" onClick={onDownloadDiagnostics} className="mt-3 rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs font-medium text-white">{t('exportRedactedDiagnosticBundle')}</button>
        {diagnosticStatus && <div role="status" className="mt-2 text-xs text-[#6d6258]">{diagnosticStatus}</div>}
      </section>
      <div className="mt-5 text-xs font-semibold text-[#7a7065]">{t('modelRoles')}</div>
      <div className="mt-2 space-y-2">{providers.map((provider) => <div key={provider.name} className="rounded-lg border border-[#ded6ca] bg-[#fbfaf7] p-3"><div className="flex items-center justify-between text-sm font-medium"><span>{provider.name}</span><span className={`status-dot status-${provider.status}`} /></div><div className="mt-1 text-xs text-[#7a7065]">{provider.role} · {provider.persona}</div></div>)}</div>
    </div>
  );
}
