# AetherChat 插件系统

AetherChat 0.2.0 提供声明式插件中心，支持：

- 查看插件名称、版本、权限和动作；
- 启用或停用插件；
- 调用插件动作并查看结构化结果；
- 从 JSON manifest 安装、更新或卸载本地插件；
- 将已启用插件动作暴露为模型可用的命名空间工具；
- 在“运行”面板执行无模型 API 的插件基础检查。

## 安全模型

内置插件可以调用后端中明确列入允许清单的处理器。用户导入的插件只能使用
`template` 处理器，不会执行 Python、JavaScript、Shell、下载文件或第三方二进制。
所有插件必须声明权限；外部链接仅允许 `http://` 和 `https://`。

插件状态保存在本机 `backend-data/plugins/` 目录中。API 密钥不属于插件配置，
也不会通过插件列表接口返回。

## Manifest 示例

```json
{
  "id": "local-note-helper",
  "name": "本地备注助手",
  "version": "1.0.0",
  "description": "把输入内容格式化为本地备注。",
  "author": "AetherChat User",
  "category": "Productivity",
  "icon": "FileText",
  "permissions": [],
  "actions": [
    {
      "id": "echo_note",
      "label": "生成备注",
      "description": "声明式文本模板，不执行代码。",
      "handler": "template",
      "input_schema": {
        "type": "object",
        "required": ["text"],
        "properties": {
          "text": { "type": "string", "title": "备注内容" }
        }
      },
      "output_template": "备注：${text}"
    }
  ]
}
```

将 JSON 粘贴到“插件 → 导入本地插件 manifest”即可安装。

## 内置兼容插件

- 浏览器链接：验证并打开 HTTP(S) 链接；
- Windows 窗口检查：只读列出可见窗口；
- 文档、PDF、表格、演示文稿：扫描当前会话工件；
- GitHub / Git 只读助手：读取分支和变更摘要；
- 任务可视化：生成六阶段图表数据；
- 应用 QA 诊断：汇总工件、任务、提案和工具状态。

这些插件复刻的是通用能力与交互方式，不包含或复制 Codex/OpenAI 的专有插件运行时。
