import { useEffect, useState } from 'react';
import { Images, Puzzle, Trash2 } from 'lucide-react';
import { asArray, fetchJson } from '../api/client';
import { confirmAction } from './confirmDialog';
import { useI18n } from '../i18n';
import type { PluginInvokeResult, PluginItem } from '../types';

type ComfyUIImageModel = {
  name: string;
  kind: 'checkpoint' | 'diffusion_model';
  recommended: boolean;
};

type ComfyUIVideoPipeline = {
  id: 'wan-i2v' | 'ltx-t2v' | 'minimax-h3';
  label: string;
  installed: boolean;
  available: boolean;
  missing: string[];
  requires_input_image: boolean;
  warning: string;
  minimum_free_memory_bytes?: number;
  current_free_memory_bytes?: number;
};

type ComfyUIOverview = {
  ready: boolean;
  runtime: {
    runtime?: string;
    autostart_enabled?: boolean;
    diagnostics?: ComfyUIOverview['diagnostics'];
  };
  diagnostics: {
    reason?: string;
    message?: string;
    unit_active?: boolean;
    docker?: {
      container?: string;
      status?: string;
      oom_killed?: boolean;
      memory_limit_bytes?: number | null;
      comfyui_process_running?: boolean;
    } | null;
  };
  selection: {
    image_model?: string | null;
    video_pipeline?: 'wan-i2v' | 'ltx-t2v' | null;
  };
  catalog: {
    image_models: ComfyUIImageModel[];
    video_pipelines: ComfyUIVideoPipeline[];
    assets?: Record<string, Array<string | { name?: string }>>;
    system?: { ram_free?: number; ram_total?: number };
  } | null;
};

const formatGiB = (value?: number | null) => (
  value ? `${(value / 1024 ** 3).toFixed(1)} GiB` : '未知'
);

export function PluginCenter({ apiBase, sessionId }: { apiBase: string; sessionId: string }) {
  const { ui } = useI18n();
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState('');
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [lastResult, setLastResult] = useState<PluginInvokeResult | null>(null);
  const [status, setStatus] = useState('');
  const [manifestText, setManifestText] = useState('');
  const [comfyOverview, setComfyOverview] = useState<ComfyUIOverview | null>(null);
  const [comfyBusy, setComfyBusy] = useState<'status' | 'start' | 'save' | ''>('');
  const [comfyStatus, setComfyStatus] = useState('');
  const [comfyImageModel, setComfyImageModel] = useState('');
  const [comfyVideoPipeline, setComfyVideoPipeline] = useState<'wan-i2v' | 'ltx-t2v' | 'minimax-h3'>('wan-i2v');

  const applyComfyOverview = (overview: ComfyUIOverview) => {
    const safeCatalog = overview.catalog ? {
      ...overview.catalog,
      image_models: asArray<ComfyUIImageModel>(overview.catalog.image_models),
      video_pipelines: asArray<ComfyUIVideoPipeline>(overview.catalog.video_pipelines),
      assets: overview.catalog.assets && typeof overview.catalog.assets === 'object'
        ? overview.catalog.assets
        : {},
      system: overview.catalog.system || {},
    } : null;
    const safeOverview = {
      ...overview,
      selection: overview.selection || {},
      catalog: safeCatalog,
    };
    setComfyOverview(safeOverview);
    const recommendedImage = safeCatalog?.image_models.find((item) => item.recommended)
      || safeCatalog?.image_models[0];
    const selectedImage = safeCatalog?.image_models.find(
      (item) => item.name === safeOverview.selection.image_model,
    );
    setComfyImageModel(selectedImage?.name || recommendedImage?.name || '');
    const installedPipeline = safeCatalog?.video_pipelines.find((item) => item.installed);
    const selectedPipeline = safeCatalog?.video_pipelines.find(
      (item) => item.installed && item.id === safeOverview.selection.video_pipeline,
    );
    setComfyVideoPipeline(
      selectedPipeline?.id || installedPipeline?.id || 'wan-i2v',
    );
  };

  const refreshComfyUI = async (start: boolean) => {
    setComfyBusy(start ? 'start' : 'status');
    try {
      const overview = await fetchJson<ComfyUIOverview>(
        `${apiBase}/plugins/comfyui/runtime/${start ? 'start' : 'status'}`,
        start ? { method: 'POST' } : undefined,
      );
      applyComfyOverview(overview);
      setComfyStatus(start ? 'ComfyUI 已启动并完成模型扫描' : '');
    } catch (error) {
      setComfyStatus(`${start ? '启动' : '状态检查'}失败：${String(error)}`);
      try {
        const overview = await fetchJson<ComfyUIOverview>(`${apiBase}/plugins/comfyui/runtime/status`);
        applyComfyOverview(overview);
      } catch {
        // Keep the actionable start error when even the diagnostic endpoint is unavailable.
      }
    } finally {
      setComfyBusy('');
    }
  };

  const saveComfySelection = async () => {
    setComfyBusy('save');
    try {
      const overview = await fetchJson<ComfyUIOverview>(`${apiBase}/plugins/comfyui/selection`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_model: comfyImageModel || null,
          video_pipeline: comfyVideoPipeline || null,
        }),
      });
      applyComfyOverview(overview);
      setComfyStatus('模型选择已保存；后续模型调用将优先使用该配置');
    } catch (error) {
      setComfyStatus(`保存模型选择失败：${String(error)}`);
    } finally {
      setComfyBusy('');
    }
  };

  const load = async () => {
    setLoading(true);
    try {
      const value = await fetchJson<unknown>(`${apiBase}/plugins/`);
      setPlugins(asArray<PluginItem>(value));
      setStatus('');
    } catch (error) {
      setStatus(`插件加载失败：${String(error)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    void refreshComfyUI(false);
  }, [apiBase]);

  const toggle = async (plugin: PluginItem) => {
    setBusyKey(plugin.id);
    try {
      const updated = await fetchJson<PluginItem>(`${apiBase}/plugins/${encodeURIComponent(plugin.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !plugin.enabled }),
      });
      setPlugins((current) => current.map((item) => item.id === updated.id ? updated : item));
      setStatus(`${plugin.name} 已${updated.enabled ? '启用' : '停用'}`);
    } catch (error) {
      setStatus(`更新失败：${String(error)}`);
    } finally {
      setBusyKey('');
    }
  };

  const invoke = async (plugin: PluginItem, action: PluginItem['actions'][number]) => {
    const actionKey = `${plugin.id}:${action.id}`;
    const argumentsForAction: Record<string, unknown> = {};
    for (const [name, property] of Object.entries(action.input_schema?.properties || {})) {
      const raw = inputs[`${actionKey}:${name}`] ?? '';
      if (!raw && !(action.input_schema.required || []).includes(name)) continue;
      if (property.type === 'integer') argumentsForAction[name] = Number.parseInt(raw, 10);
      else if (property.type === 'number') argumentsForAction[name] = Number.parseFloat(raw);
      else if (property.type === 'boolean') argumentsForAction[name] = raw === 'true';
      else if (property.type === 'object' || property.type === 'array') {
        try {
          argumentsForAction[name] = JSON.parse(raw);
        } catch {
          setStatus(`${action.label} 的 ${name} 必须是有效 JSON`);
          return;
        }
      }
      else argumentsForAction[name] = raw;
    }
    setBusyKey(actionKey);
    try {
      const result = await fetchJson<PluginInvokeResult>(`${apiBase}/plugins/${encodeURIComponent(plugin.id)}/invoke`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: action.id, session_id: sessionId, arguments: argumentsForAction }),
      });
      setLastResult(result);
      const payload = result.result as {
        kind?: string;
        url?: string;
        browser?: 'default' | 'firefox';
      } | null;
      if (payload?.kind === 'open_external' && payload.url) {
        if (!window.aetherRuntime?.openExternal) throw new Error('当前不是可打开外部链接的桌面运行环境');
        await window.aetherRuntime.openExternal(payload.url, {
          browser: payload.browser || 'default',
        });
      }
      setStatus(`${plugin.name} · ${action.label} 执行成功`);
    } catch (error) {
      setStatus(`调用失败：${String(error)}`);
    } finally {
      setBusyKey('');
    }
  };

  const install = async () => {
    try {
      const manifest = JSON.parse(manifestText);
      const installed = await fetchJson<PluginItem>(`${apiBase}/plugins/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(manifest),
      });
      setPlugins((current) => [installed, ...current.filter((item) => item.id !== installed.id)]);
      setManifestText('');
      setStatus(`插件 ${installed.name} 已安装`);
    } catch (error) {
      setStatus(`安装失败：${String(error)}`);
    }
  };

  const uninstall = async (plugin: PluginItem) => {
    if (!await confirmAction(`确定卸载插件“${plugin.name}”吗？`, { danger: true })) return;
    try {
      await fetchJson(`${apiBase}/plugins/${encodeURIComponent(plugin.id)}`, { method: 'DELETE' });
      setPlugins((current) => current.filter((item) => item.id !== plugin.id));
      setStatus(`${plugin.name} 已卸载`);
    } catch (error) {
      setStatus(`卸载失败：${String(error)}`);
    }
  };

  return (
    <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold"><Puzzle size={16} />{ui('插件中心')}</div>
          <div className="mt-1 text-xs text-[#7a7065]">{ui('兼容 Codex 风格 manifest；自定义插件仅支持声明式模板，不执行任意代码。')}</div>
        </div>
        <button onClick={load} disabled={loading} className="rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-50">{ui('刷新')}</button>
      </div>

      {status && <div className="mb-3 rounded-lg border border-[#d8cfc3] bg-[#fffdf9] px-3 py-2 text-xs text-[#6d6258]">{ui(status)}</div>}

      <section className="mb-4 rounded-xl border border-[#cdbda9] bg-[#fffaf3] p-4" aria-label={ui('ComfyUI 模型选择')}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Images size={16} />{ui('ComfyUI 运行与模型选择')}
              <span className={`rounded px-2 py-0.5 text-[10px] ${comfyOverview?.ready ? 'bg-[#dcebd7] text-[#326022]' : 'bg-[#ead5d1] text-[#8a3b31]'}`}>
                {comfyOverview?.ready ? ui('已就绪') : ui('未就绪')}
              </span>
            </div>
            <div className="mt-1 text-xs leading-5 text-[#7a7065]">
              {ui('启动后读取 ComfyUI 实际安装的模型；保存的选择优先于模型自动选择。')}
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => refreshComfyUI(false)} disabled={Boolean(comfyBusy)} className="rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-40">
              {comfyBusy === 'status' ? ui('检查中') : ui('检查状态')}
            </button>
            <button onClick={() => refreshComfyUI(true)} disabled={Boolean(comfyBusy)} className="rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs text-white disabled:opacity-40">
              {comfyBusy === 'start' ? ui('启动与扫描中') : ui('启动并扫描模型')}
            </button>
          </div>
        </div>

        {(comfyStatus || comfyOverview?.diagnostics?.message) && (
          <div role="status" className="mt-3 rounded-md border border-[#dfd2c2] bg-white px-3 py-2 text-xs leading-5 text-[#6d6258]">
            {comfyStatus || comfyOverview?.diagnostics?.message}
          </div>
        )}
        {comfyOverview?.diagnostics?.docker?.oom_killed && (
          <div className="mt-2 rounded-md border border-[#d7a98b] bg-[#fff0e5] px-3 py-2 text-xs leading-5 text-[#8a3b31]">
            {ui('检测到 ComfyUI 容器曾发生 OOM：视频大模型可能与本地语言模型争用统一内存。 LTX 22B 只有在可用内存达到安全阈值时才允许执行。')}
          </div>
        )}

        {comfyOverview?.catalog && (
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <label className="text-xs font-semibold text-[#62584e]">
              {ui('图像生成模型')}
              <select aria-label={ui('ComfyUI 图像生成模型')} value={comfyImageModel} onChange={(event) => setComfyImageModel(event.target.value)} className="mt-2 h-9 w-full rounded-md border border-[#d6cbbd] bg-white px-2 text-xs font-normal">
                {comfyOverview.catalog.image_models.map((model) => (
                  <option key={model.name} value={model.name}>
                    {model.name}{model.recommended ? ui('（推荐）') : ''}
                  </option>
                ))}
              </select>
              <span className="mt-1 block font-normal text-[#8b8176]">{ui('Checkpoint 与 Qwen-Image 均来自当前 ComfyUI 实际清单。')}</span>
            </label>

            <label className="text-xs font-semibold text-[#62584e]">
              {ui('视频生成管线')}
              <select aria-label={ui('ComfyUI 视频生成管线')} value={comfyVideoPipeline} onChange={(event) => setComfyVideoPipeline(event.target.value as 'wan-i2v' | 'ltx-t2v' | 'minimax-h3')} className="mt-2 h-9 w-full rounded-md border border-[#d6cbbd] bg-white px-2 text-xs font-normal">
                {comfyOverview.catalog.video_pipelines.filter((pipeline) => pipeline.installed).map((pipeline) => (
                  <option key={pipeline.id} value={pipeline.id}>
                    {pipeline.label}{pipeline.available ? '' : ui('（当前资源不足）')}
                  </option>
                ))}
              </select>
              <span className="mt-1 block font-normal text-[#8b8176]">
                {ui('当前系统可用内存：{value}', { value: ui(formatGiB(comfyOverview.catalog.system?.ram_free)) })}
              </span>
            </label>

            <div className="space-y-2 lg:col-span-2">
              {comfyOverview.catalog.video_pipelines.map((pipeline) => (
                <div key={pipeline.id} data-comfy-pipeline={pipeline.id} className={`rounded-md border px-3 py-2 text-xs leading-5 ${pipeline.available ? 'border-[#c6d8c3] bg-[#f2f8ef] text-[#4d6848]' : 'border-[#dfb69d] bg-[#fff2e8] text-[#8a4a32]'}`}>
                  <span className="font-semibold">{pipeline.label}</span>
                  <span className="ml-2">{pipeline.installed ? pipeline.available ? ui('可运行') : ui('已安装但资源不足') : ui('缺少依赖')}</span>
                  <div>{pipeline.warning}</div>
                  {pipeline.missing.length > 0 && <div>{ui('缺少：{value}', { value: pipeline.missing.join('、') })}</div>}
                  {pipeline.minimum_free_memory_bytes && <div>{ui('安全阈值：{minimum}；当前：{current}', { minimum: formatGiB(pipeline.minimum_free_memory_bytes), current: formatGiB(pipeline.current_free_memory_bytes) })}</div>}
                </div>
              ))}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 lg:col-span-2">
              <button onClick={saveComfySelection} disabled={Boolean(comfyBusy) || !comfyImageModel} className="rounded-md bg-[#8d5f35] px-3 py-1.5 text-xs text-white disabled:opacity-40">
                {comfyBusy === 'save' ? ui('保存中') : ui('保存模型选择')}
              </button>
              <span className="text-[11px] text-[#8b8176]">
                {ui('已发现 {images} 个可用图像模型、{pipelines} 条已安装视频管线', { images: comfyOverview.catalog.image_models.length, pipelines: comfyOverview.catalog.video_pipelines.filter((item) => item.installed).length })}
              </span>
            </div>

            <details className="rounded-md border border-[#ded6ca] bg-white p-3 text-xs lg:col-span-2">
              <summary className="cursor-pointer font-semibold">{ui('查看全部已安装模型资源')}</summary>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                {Object.entries(comfyOverview.catalog.assets || {}).map(([group, values]) => (
                  <div key={group}>
                    <div className="font-semibold text-[#62584e]">{group} · {values.length}</div>
                    <div className="mt-1 max-h-28 overflow-auto whitespace-pre-line break-all text-[11px] leading-5 text-[#7a7065]">
                      {values.map((value) => typeof value === 'string' ? value : value.name || 'unnamed').join('\n') || ui('无')}
                    </div>
                  </div>
                ))}
              </div>
            </details>
          </div>
        )}
      </section>

      <div className="space-y-3">
        {plugins.map((plugin) => (
          <article key={plugin.id} className="rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold">{plugin.name}</span>
                  <span className="rounded bg-[#ebe3d8] px-1.5 py-0.5 text-[10px]">v{plugin.version}</span>
                  <span className="rounded bg-[#eee7de] px-1.5 py-0.5 text-[10px]">{plugin.builtin ? ui('内置') : ui('本地')}</span>
                </div>
                <div className="mt-1 text-xs leading-5 text-[#7a7065]">{plugin.description}</div>
              </div>
              <button
                onClick={() => toggle(plugin)}
                disabled={busyKey === plugin.id}
                className={`shrink-0 rounded-md px-2.5 py-1 text-xs ${plugin.enabled ? 'bg-[#2f2a24] text-white' : 'border border-[#d6cbbd] text-[#786d62]'}`}
              >
                {plugin.enabled ? ui('已启用') : ui('已停用')}
              </button>
            </div>

            <div className="mt-3 flex flex-wrap gap-1.5">
              {plugin.permissions.map((permission) => <span key={permission} className="rounded-md border border-[#ded6ca] px-2 py-1 text-[10px] text-[#7a7065]">{permission}</span>)}
              {!plugin.permissions.length && <span className="text-[10px] text-[#8b8176]">{ui('无需额外权限')}</span>}
            </div>

            <div className="mt-3 space-y-2">
              {plugin.actions.map((action) => {
                const actionKey = `${plugin.id}:${action.id}`;
                const properties = Object.entries(action.input_schema?.properties || {});
                return (
                  <div key={action.id} className="rounded-lg border border-[#e4dbcf] bg-white p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-xs font-semibold">{action.label}</div>
                        <div className="mt-1 text-[11px] text-[#7a7065]">{action.description}</div>
                      </div>
                      <button
                        onClick={() => invoke(plugin, action)}
                        disabled={!plugin.enabled || busyKey === actionKey}
                        className="shrink-0 rounded-md bg-[#8d5f35] px-2.5 py-1 text-xs text-white disabled:opacity-40"
                      >
                        {busyKey === actionKey ? ui('执行中') : ui('运行')}
                      </button>
                    </div>
                    {properties.length > 0 && (
                      <div className="mt-2 grid gap-2">
                        {properties.map(([name, property]) => (
                          <input
                            key={name}
                            value={inputs[`${actionKey}:${name}`] ?? ''}
                            onChange={(event) => setInputs((current) => ({ ...current, [`${actionKey}:${name}`]: event.target.value }))}
                            placeholder={ui('{name}{required}', {
                              name: property.title || name,
                              required: (action.input_schema.required || []).includes(name) ? ui('（必填）') : '',
                            })}
                            className="h-8 rounded-md border border-[#ded6ca] px-2 text-xs outline-none"
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {!plugin.builtin && (
              <button onClick={() => uninstall(plugin)} className="mt-3 flex items-center gap-1 text-xs text-[#8a3428]"><Trash2 size={12} />{ui('卸载')}</button>
            )}
          </article>
        ))}
        {!loading && plugins.length === 0 && <div className="rounded-lg border border-dashed border-[#d6cbbd] p-6 text-center text-sm text-[#7a7065]">{ui('暂无插件')}</div>}
      </div>

      {lastResult && (
        <div className="mt-4 rounded-xl border border-[#ded6ca] bg-[#2f2a24] p-3 text-[#f6eee4]">
          <div className="text-xs font-semibold">{ui('最近结果 · {name}', { name: lastResult.plugin_name })}</div>
          <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-all text-[11px] leading-5">{JSON.stringify(
            lastResult.result,
            (key, value) => key === 'image_data_url' && typeof value === 'string'
              ? `[PNG 视觉数据已省略，${value.length} 字符]`
              : value,
            2,
          )}</pre>
        </div>
      )}

      <details className="mt-4 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3">
        <summary className="cursor-pointer text-xs font-semibold">{ui('导入本地插件 manifest')}</summary>
        <textarea
          value={manifestText}
          onChange={(event) => setManifestText(event.target.value)}
          placeholder={ui('粘贴 JSON，例如：{"id":"my-plugin","name":"我的插件","version":"1.0.0",...}')}
          className="mt-3 h-36 w-full resize-y rounded-lg border border-[#ded6ca] bg-white p-2 font-mono text-[11px] outline-none"
        />
        <button onClick={install} disabled={!manifestText.trim()} className="mt-2 rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs text-white disabled:opacity-40">{ui('安装 manifest')}</button>
      </details>
    </div>
  );
}

