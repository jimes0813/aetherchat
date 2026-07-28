'use strict';

function buildContextMenuTemplate(params = {}) {
  const editable = Boolean(params.isEditable);
  const hasSelection = Boolean(String(params.selectionText || '').trim());
  if (editable) {
    return [
      { role: 'undo', label: '撤销', enabled: Boolean(params.editFlags?.canUndo) },
      { role: 'redo', label: '重做', enabled: Boolean(params.editFlags?.canRedo) },
      { type: 'separator' },
      { role: 'cut', label: '剪切', enabled: Boolean(params.editFlags?.canCut) },
      { role: 'copy', label: '复制', enabled: Boolean(params.editFlags?.canCopy || hasSelection) },
      { role: 'paste', label: '粘贴', enabled: Boolean(params.editFlags?.canPaste) },
      { role: 'delete', label: '删除', enabled: Boolean(params.editFlags?.canDelete) },
      { type: 'separator' },
      { role: 'selectAll', label: '全选', enabled: Boolean(params.editFlags?.canSelectAll ?? true) },
    ];
  }
  return [
    { role: 'copy', label: '复制', enabled: hasSelection },
    { role: 'selectAll', label: '全选', enabled: true },
  ];
}

module.exports = { buildContextMenuTemplate };
