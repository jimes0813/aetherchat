import { describe, expect, it } from 'vitest';
import { compactToolName, parseToolActivity } from './toolDisplay';

describe('toolDisplay', () => {
  it('shortens plugin tool names', () => {
    expect(compactToolName('plugin__comfyui__generate_image')).toBe('generate_image');
    expect(compactToolName('run_command')).toBe('run_command');
  });

  it('parses used tools from reasoning steps', () => {
    const rows = parseToolActivity([
      { phase: 'tool', detail: '已选择调用 plugin__comfyui__generate_image，等待授权或本机执行结果。' },
      { phase: 'result', detail: '工具 plugin__comfyui__generate_image 执行成功，正在依据真实结果继续处理。' },
      { phase: 'final', detail: '已核对模型输出与工具结果。' },
    ]);
    expect(rows).toEqual([
      expect.objectContaining({ label: 'generate_image', status: 'used' }),
    ]);
  });
});
