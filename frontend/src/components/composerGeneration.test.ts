import { describe, expect, it } from 'vitest';
import {
  buildComposerMessage,
  choiceHasStages,
  listVideoModelChoices,
  shortModelLabel,
  videoStageFilename,
} from './composerGeneration';

const remixHigh = 'Wan2.2_Remix_NSFW_i2v_14b_high_lighting_fp8_e4m3fn_v3.0.safetensors';
const remixLow = 'Wan2.2_Remix_NSFW_i2v_14b_low_lighting_fp8_e4m3fn_v3.0.safetensors';

describe('composerGeneration', () => {
  it('builds explicit image and video prompts with full filenames', () => {
    expect(buildComposerMessage({
      intent: 'image',
      text: 'a rainy street',
      imageModel: 'qwen_image_2512_fp8_e4m3fn.safetensors',
    })).toBe('使用 qwen_image_2512_fp8_e4m3fn.safetensors 生成图片\na rainy street');

    expect(buildComposerMessage({
      intent: 'video',
      text: 'slow camera push in',
      videoModel: remixHigh,
    })).toBe(`使用 ${remixHigh} 生成视频\nslow camera push in`);
  });

  it('uses a default motion line when video has an attached image but no text', () => {
    expect(buildComposerMessage({
      intent: 'video',
      text: '',
      videoModel: remixLow,
      hasImageAttachment: true,
    })).toBe(`使用 ${remixLow} 生成视频\n让上面的图片动起来`);
  });

  it('prefixes code requests without touching ComfyUI wording', () => {
    expect(buildComposerMessage({
      intent: 'code',
      text: '用 Python 写去重函数',
    })).toBe('请编写代码并给出可运行实现。\n用 Python 写去重函数');
  });

  it('lists installed video models and exposes remix high/low files', () => {
    const choices = listVideoModelChoices([
      {
        id: 'wan-i2v',
        label: 'Wan I2V',
        installed: true,
        models: [
          { name: 'wan2.1_i2v_480p_14B_fp8_scaled.safetensors', installed: true, label: 'Wan 2.1 I2V 14B' },
          { name: remixHigh, installed: true, label: 'Wan 2.2 Remix', companion_models: [remixLow] },
        ],
      },
      { id: 'ltx-t2v', label: 'LTX 2.3', installed: false },
    ]);
    const remix = choices.find((item) => item.name === remixHigh);
    expect(choices.map((item) => item.name)).toEqual([
      'wan2.1_i2v_480p_14B_fp8_scaled.safetensors',
      remixHigh,
      remixLow,
    ]);
    expect(choiceHasStages(remix)).toBe(true);
    expect(videoStageFilename(remix, 'high')).toBe(remixHigh);
    expect(videoStageFilename(remix, 'low')).toBe(remixLow);
    expect(shortModelLabel(remixHigh)).toBe('Remix High');
    expect(shortModelLabel('minimax_h3_fl2va_pruned_int8_convrot.safetensors')).toBe('MiniMax H3');
  });

  it('loads catalog after auth and starts ComfyUI when the first status is empty', async () => {
    const calls: string[] = [];
    const getJson = async <T>(url: string) => {
      calls.push(url);
      if (url.endsWith('/status')) {
        return { ready: false, catalog: null } as T;
      }
      return {
        ready: true,
        catalog: {
          image_models: [{ name: 'qwen_image_2512_fp8_e4m3fn.safetensors' }],
          video_pipelines: [],
        },
      } as T;
    };
    const { loadComfyGenerationCatalog } = await import('./composerGeneration');
    const catalog = await loadComfyGenerationCatalog('http://localhost:8000', {
      startIfNeeded: true,
      getJson,
    });
    expect(calls).toEqual([
      'http://localhost:8000/plugins/comfyui/runtime/status',
      'http://localhost:8000/plugins/comfyui/runtime/start',
    ]);
    expect(catalog.image_models[0]?.name).toBe('qwen_image_2512_fp8_e4m3fn.safetensors');
  });
});
