import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useSessionStore } from '../stores/sessionStore';
import { Composer } from './Composer';

const remixHigh = 'Wan2.2_Remix_NSFW_i2v_14b_high_lighting_fp8_e4m3fn_v3.0.safetensors';
const remixLow = 'Wan2.2_Remix_NSFW_i2v_14b_low_lighting_fp8_e4m3fn_v3.0.safetensors';
const qwen = 'qwen_image_2512_fp8_e4m3fn.safetensors';

const catalog = {
  image_models: [
    { name: 'RealVisXL_V5.0_Lightning_fp16.safetensors', recommended: true },
    { name: qwen, recommended: false },
  ],
  video_pipelines: [{
    id: 'wan-i2v',
    label: 'Wan I2V',
    installed: true,
    models: [
      { name: remixHigh, installed: true, label: 'Wan 2.2 Remix', companion_models: [remixLow] },
    ],
  }],
};

function renderComposer(onSend = vi.fn(() => true)) {
  render(
    <Composer
      providers={['Qwen']}
      onSend={onSend}
      onUpload={vi.fn(async () => [])}
      generationCatalog={catalog}
    />,
  );
  return onSend;
}

describe('Composer generation toolbar', () => {
  it('keeps the draft and opens model setup when no model is available', () => {
    const onSend = vi.fn(() => true);
    const onConfigureModels = vi.fn();
    render(
      <Composer providers={[]} onSend={onSend} onUpload={vi.fn(async () => [])} onConfigureModels={onConfigureModels} />,
    );
    const input = screen.getByLabelText('问点什么，输入 @ 引用本对话中的消息、图片或文档…');
    fireEvent.change(input, { target: { value: '保留这份草稿' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue('保留这份草稿');
    fireEvent.click(screen.getByRole('button', { name: '配置模型' }));
    expect(onConfigureModels).toHaveBeenCalled();
  });

  it('shows code, image, and video actions in the chat box', () => {
    renderComposer();
    expect(screen.getByRole('button', { name: '代码' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '图片生成' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '视频生成' })).toBeTruthy();
  });

  it('opens ChatGPT-style voice mode from the composer', () => {
    const onStartVoice = vi.fn();
    render(
      <Composer
        providers={['Qwen']}
        onSend={vi.fn(() => true)}
        onUpload={vi.fn(async () => [])}
        onStartVoice={onStartVoice}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '开始语音对话' }));
    expect(onStartVoice).toHaveBeenCalled();
  });

  it('lets the user pick an image model and sends the full filename', () => {
    const onSend = renderComposer();
    fireEvent.click(screen.getByRole('button', { name: '图片生成' }));
    fireEvent.click(screen.getByRole('option', { name: /Qwen Image/ }));
    fireEvent.change(screen.getByLabelText('描述主体、构图、光线和风格'), {
      target: { value: '雨夜街头' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(onSend).toHaveBeenCalledWith(
      `使用 ${qwen} 生成图片\n雨夜街头`,
      ['all'],
      [],
    );
  });

  it('lets the user pick Remix Low and sends that video file', () => {
    const onSend = renderComposer();
    fireEvent.click(screen.getByRole('button', { name: '视频生成' }));
    expect(screen.getByRole('button', { name: 'High' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Low' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Low' }));
    fireEvent.change(screen.getByLabelText('描述动作、镜头运动和画面；附图将作为起始帧'), {
      target: { value: '人物慢慢转身' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(onSend).toHaveBeenCalledWith(
      `使用 ${remixLow} 生成视频\n人物慢慢转身`,
      ['all'],
      [],
    );
  });

  it('still shows High/Low when ComfyUI catalog has not loaded', () => {
    render(
      <Composer
        providers={['Qwen']}
        onSend={vi.fn(() => true)}
        onUpload={vi.fn(async () => [])}
        generationCatalog={{ image_models: [], video_pipelines: [] }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '视频生成' }));
    expect(screen.getByRole('button', { name: 'High' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Low' })).toBeTruthy();
    expect(screen.getByRole('listbox', { name: '视频模型' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Remix High' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Remix Low' })).toBeTruthy();
  });

  it('renders the model card outside the composer so the input size stays fixed', () => {
    renderComposer();
    fireEvent.click(screen.getByRole('button', { name: '图片生成' }));
    const card = screen.getByTestId('generation-model-card');
    expect(card.closest('[data-aether-composer-card]')).toBeNull();
    expect(document.body.contains(card)).toBe(true);
  });

  it('prefixes code mode without requiring a ComfyUI model', () => {
    const onSend = renderComposer();
    fireEvent.click(screen.getByRole('button', { name: '代码' }));
    fireEvent.change(screen.getByLabelText('描述要写的代码、语言和目标'), {
      target: { value: '写一个去重函数' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(onSend).toHaveBeenCalledWith(
      '请编写代码并给出可运行实现。\n写一个去重函数',
      ['all'],
      [],
    );
  });

  it('lets the user cite a message from the current conversation', () => {
    useSessionStore.setState({
      sessionId: 'current-session',
      messages: [
        {
          id: 'msg-cover',
          session_id: 'current-session',
          author_model: 'user',
          role: 'user',
          content: '封面要用雨夜街头',
          created_at: '2026-01-01T00:00:00Z',
        },
      ],
    });
    const onSend = renderComposer();
    fireEvent.click(screen.getByRole('button', { name: '引用当前对话中的消息、图片或文档' }));
    fireEvent.click(screen.getByRole('option', { name: /封面要用雨夜街头/ }));
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(onSend).toHaveBeenCalledWith(
      '[@消息:封面要用雨夜街头]',
      ['all'],
      [],
      [expect.objectContaining({ kind: 'message', id: 'msg-cover', label: '封面要用雨夜街头' })],
    );
  });
});
