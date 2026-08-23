import { fetchJson } from '../api/client';

const EXAMPLE_FILES = [
  {
    path: '示例/README.md',
    content: '# 示例项目\n\n这是显式创建的 AetherChat 示例，可安全编辑或另存为自己的项目。\n',
  },
  {
    path: '示例/docs/计划.md',
    content: '# 示例计划\n\n- [ ] 明确目标\n- [ ] 分配任务\n- [ ] 审核交付物\n',
  },
  {
    path: '示例/src/demo.ts',
    content: "export const example = 'AetherChat explicit sample';\n",
  },
];

export async function createExampleArtifacts(apiBase: string, sessionId: string) {
  await Promise.all(EXAMPLE_FILES.map((file) => fetchJson(`${apiBase}/artifacts/write`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      path: file.path,
      content: file.content,
      commit_message: '[示例] 创建教程文件',
      author: 'example-template',
      status: 'ready',
    }),
  })));
}
