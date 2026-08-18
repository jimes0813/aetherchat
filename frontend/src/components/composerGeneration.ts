import { fetchJson } from '../api/client';

export type ComposerIntent = 'chat' | 'code' | 'image' | 'video';
export type VideoStage = 'high' | 'low';

export type ComfyImageModel = {
  name: string;
  kind?: string;
  recommended?: boolean;
};

export type ComfyVideoModel = {
  name: string;
  label?: string;
  installed?: boolean;
  recommended?: boolean;
  companion_models?: string[];
};

export type ComfyVideoPipeline = {
  id: string;
  label: string;
  installed: boolean;
  available?: boolean;
  models?: ComfyVideoModel[];
};

export type ComfyGenerationCatalog = {
  image_models: ComfyImageModel[];
  video_pipelines: ComfyVideoPipeline[];
};

export type VideoModelChoice = {
  name: string;
  label: string;
  pipelineId: string;
  companionModels: string[];
};

export function shortModelLabel(name: string): string {
  const stem = String(name || '').replace(/\.safetensors$/i, '');
  if (/minimax|h3/i.test(stem)) return 'MiniMax H3';
  if (/qwen_image/i.test(stem)) return 'Qwen Image';
  if (/realvisxl.*lightning/i.test(stem)) return 'RealVisXL Lightning';
  if (/realvis/i.test(stem)) return 'RealVisXL';
  if (/bigasp/i.test(stem)) return 'BigASP v2';
  if (/pony/i.test(stem)) return 'Pony Diffusion';
  if (/remix.*low/i.test(stem)) return 'Remix Low';
  if (/remix.*high|remix/i.test(stem)) return 'Remix High';
  if (/wan2[._-]?1/i.test(stem)) return 'Wan 2.1 I2V';
  if (/ltx/i.test(stem)) return 'LTX 2.3';
  return stem || name;
}

export const REMIX_HIGH_MODEL = 'Wan2.2_Remix_NSFW_i2v_14b_high_lighting_fp8_e4m3fn_v3.0.safetensors';
export const REMIX_LOW_MODEL = 'Wan2.2_Remix_NSFW_i2v_14b_low_lighting_fp8_e4m3fn_v3.0.safetensors';

export function listVideoModelChoices(pipelines: ComfyVideoPipeline[] = []): VideoModelChoice[] {
  const choices: VideoModelChoice[] = [];
  const seen = new Set<string>();
  const add = (choice: VideoModelChoice) => {
    if (!choice.name || seen.has(choice.name)) return;
    seen.add(choice.name);
    choices.push(choice);
  };
  for (const pipeline of pipelines) {
    if (!pipeline.installed) continue;
    const models = (pipeline.models || []).filter((item) => item.installed !== false && item.name);
    if (models.length === 0) {
      if (pipeline.id === 'ltx-t2v') {
        add({
          name: 'ltx-2.3-22b-dev.safetensors',
          label: pipeline.label || 'LTX 2.3',
          pipelineId: pipeline.id,
          companionModels: [],
        });
      }
      continue;
    }
    for (const model of models) {
      const companions = (model.companion_models || []).filter(Boolean);
      add({
        name: model.name,
        label: model.label || shortModelLabel(model.name),
        pipelineId: pipeline.id,
        companionModels: companions,
      });
      for (const companion of companions) {
        add({
          name: companion,
          label: shortModelLabel(companion),
          pipelineId: pipeline.id,
          companionModels: [],
        });
      }
    }
  }
  return choices;
}

export type ComfyOverviewPayload = {
  ready?: boolean;
  catalog?: ComfyGenerationCatalog | null;
};

export async function loadComfyGenerationCatalog(
  apiBase: string,
  options?: {
    startIfNeeded?: boolean;
    getJson?: <T>(url: string, init?: RequestInit) => Promise<T>;
  },
): Promise<ComfyGenerationCatalog> {
  const getJson = options?.getJson ?? fetchJson;
  const empty: ComfyGenerationCatalog = { image_models: [], video_pipelines: [] };
  const normalize = (overview: ComfyOverviewPayload | null | undefined): ComfyGenerationCatalog => ({
    image_models: overview?.catalog?.image_models ?? [],
    video_pipelines: overview?.catalog?.video_pipelines ?? [],
  });
  const status = normalize(
    await getJson<ComfyOverviewPayload>(`${apiBase}/plugins/comfyui/runtime/status`),
  );
  if (status.image_models.length || status.video_pipelines.length) return status;
  if (!options?.startIfNeeded) return empty;
  return normalize(
    await getJson<ComfyOverviewPayload>(`${apiBase}/plugins/comfyui/runtime/start`, {
      method: 'POST',
    }),
  );
}

export function videoStageFilename(
  choice: VideoModelChoice | undefined,
  stage: VideoStage,
): string {
  if (!choice) return '';
  if (stage === 'low') {
    const low = choice.companionModels.find((name) => /low/i.test(name));
    return low || choice.companionModels[0] || choice.name;
  }
  return choice.name;
}

export function choiceHasStages(choice: VideoModelChoice | undefined): boolean {
  return Boolean(choice?.companionModels.length);
}

export function buildComposerMessage(input: {
  intent: ComposerIntent;
  text: string;
  imageModel?: string | null;
  videoModel?: string | null;
  hasImageAttachment?: boolean;
}): string {
  const text = String(input.text || '').trim();
  if (input.intent === 'image') {
    const model = String(input.imageModel || '').trim();
    const lead = model ? `使用 ${model} 生成图片` : '使用 ComfyUI 生成图片';
    return text ? `${lead}\n${text}` : lead;
  }
  if (input.intent === 'video') {
    const model = String(input.videoModel || '').trim();
    const lead = model ? `使用 ${model} 生成视频` : '使用 ComfyUI 生成视频';
    const body = text || (input.hasImageAttachment ? '让上面的图片动起来' : '');
    return body ? `${lead}\n${body}` : lead;
  }
  if (input.intent === 'code') {
    const lead = '请编写代码并给出可运行实现。';
    return text ? `${lead}\n${text}` : lead;
  }
  return text;
}
