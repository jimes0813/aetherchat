import { useEffect, useMemo, useState } from 'react';
import { fetchJson } from '../api/client';
import { useI18n } from '../i18n';
import type { ModelConfig } from '../types';
import {
  explainLocalModelFailure,
  formatLocalRuntimeLabel,
  isLocalManagedModel,
  startAndVerifyLocalModel,
  type LocalFailureGuide,
  type LocalRuntimeStatus,
} from './localModelRuntime';

export type LocalModelQuickPathProps = {
  apiBase: string;
  models: ModelConfig[];
  onNotice?: (message: string) => void;
};

export function LocalModelQuickPath({ apiBase, models, onNotice }: LocalModelQuickPathProps) {
  const { t, ui } = useI18n();
  const localModels = useMemo(
    () => models.filter(isLocalManagedModel),
    [models],
  );
  const [statuses, setStatuses] = useState<Record<string, LocalRuntimeStatus | null>>({});
  const [busy, setBusy] = useState<Record<string, '' | 'status' | 'start' | 'verify'>>({});
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [guides, setGuides] = useState<Record<string, LocalFailureGuide | null>>({});

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      for (const model of localModels) {
        setBusy((current) => ({ ...current, [model.name]: 'status' }));
        try {
          const status = await fetchJson<LocalRuntimeStatus>(
            `${apiBase}/models/${encodeURIComponent(model.name)}/runtime`,
          );
          if (!cancelled) {
            setStatuses((current) => ({ ...current, [model.name]: status }));
            if (!status.ready && status.message) {
              setGuides((current) => ({
                ...current,
                [model.name]: explainLocalModelFailure(status.message || '', status),
              }));
            }
          }
        } catch (error) {
          if (!cancelled) {
            const text = error instanceof Error ? error.message : '状态查询失败';
            setStatuses((current) => ({
              ...current,
              [model.name]: { ready: false, message: text },
            }));
            setGuides((current) => ({
              ...current,
              [model.name]: explainLocalModelFailure(text),
            }));
          }
        } finally {
          if (!cancelled) {
            setBusy((current) => ({ ...current, [model.name]: '' }));
          }
        }
      }
    };
    if (localModels.length) void load();
    return () => {
      cancelled = true;
    };
  }, [apiBase, localModels]);

  if (!localModels.length) return null;

  const runStartVerify = async (model: ModelConfig) => {
    setMessages((current) => ({ ...current, [model.name]: '' }));
    setGuides((current) => ({ ...current, [model.name]: null }));
    try {
      const result = await startAndVerifyLocalModel(
        apiBase,
        model,
        fetchJson,
        (phase) => setBusy((current) => ({ ...current, [model.name]: phase })),
      );
      setStatuses((current) => ({
        ...current,
        [model.name]: result.runtime || current[model.name] || null,
      }));
      setMessages((current) => ({ ...current, [model.name]: result.message }));
      if (!result.ok) {
        setGuides((current) => ({
          ...current,
          [model.name]: result.guide || explainLocalModelFailure(result.message, result.runtime),
        }));
        onNotice?.(`${model.name} 失败：${result.message}`);
      } else {
        setGuides((current) => ({ ...current, [model.name]: null }));
        onNotice?.(`${model.name}：${result.message}`);
      }
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setMessages((current) => ({ ...current, [model.name]: text }));
      setGuides((current) => ({
        ...current,
        [model.name]: explainLocalModelFailure(text),
      }));
      onNotice?.(`${model.name} 失败：${text}`);
    } finally {
      setBusy((current) => ({ ...current, [model.name]: '' }));
    }
  };

  return (
    <section className="mb-4 rounded-xl border border-[#d6cbbd] bg-[#f7f3ed] p-3">
      <div className="mb-2 text-xs font-semibold text-[#7a7065]">{t('localModelQuickPath')}</div>
      <p className="mb-3 text-[11px] leading-5 text-[#7a7065]">
        {t('forConfiguredLocalModelsStart')}
      </p>
      <div className="space-y-2">
        {localModels.map((model) => {
          const status = statuses[model.name];
          const phase = busy[model.name] || '';
          const label = formatLocalRuntimeLabel(status, phase);
          const ready = Boolean(status?.ready);
          const guide = guides[model.name];
          return (
            <div key={model.name} className="rounded-lg border border-[#ded6ca] bg-white p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{model.name}</div>
                  <div className="mt-1 text-xs text-[#7a7065]">
                    {model.local_runtime === 'llama_cpp' ? t('managedByLlamaCpp') : t('externalLocalEndpoint')}
                    {model.base_url ? ` · ${model.base_url}` : ''}
                  </div>
                  <div className={`mt-2 text-xs ${ready ? 'text-[#326022]' : 'text-[#7a5a3d]'}`}>
                    {label}
                  </div>
                  {messages[model.name] && ready && (
                    <div className="mt-1 text-[11px] leading-5 text-[#6d6258]">{messages[model.name]}</div>
                  )}
                </div>
                <button
                  type="button"
                  disabled={Boolean(phase)}
                  onClick={() => void runStartVerify(model)}
                  className="shrink-0 rounded-md bg-[#2f2a24] px-2 py-1 text-xs text-white disabled:opacity-50"
                >
                  {phase ? t('processing') : ready ? t('verifyAgain') : t('startAndVerify')}
                </button>
              </div>
              {guide && !ready && (
                <div
                  role="alert"
                  className="mt-3 rounded-lg border border-[#e2b8b0] bg-[#fff5f2] px-3 py-2 text-[11px] leading-5 text-[#8a4036]"
                >
                  <div className="font-medium">{guide.summary}</div>
                  {guide.steps.length > 0 && (
                    <ol className="mt-1 list-decimal space-y-0.5 pl-4">
                      {guide.steps.map((step) => (
                        <li key={step}>{step}</li>
                      ))}
                    </ol>
                  )}
                  {guide.logTail && (
                    <pre className="mt-2 max-h-20 overflow-auto whitespace-pre-wrap rounded bg-[#f8ebe8] px-2 py-1 font-mono text-[10px] text-[#6a3a34]">
                      {guide.logTail}
                    </pre>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
