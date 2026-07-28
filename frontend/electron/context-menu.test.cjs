'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { buildContextMenuTemplate } = require('./context-menu.cjs');

test('editable context menu exposes native copy and paste roles', () => {
  const template = buildContextMenuTemplate({
    isEditable: true,
    selectionText: 'AetherChat',
    editFlags: {
      canUndo: true,
      canRedo: false,
      canCut: true,
      canCopy: true,
      canPaste: true,
      canDelete: true,
      canSelectAll: true,
    },
  });
  assert.deepEqual(
    template.filter((item) => item.role).map((item) => item.role),
    ['undo', 'redo', 'cut', 'copy', 'paste', 'delete', 'selectAll'],
  );
  assert.equal(template.find((item) => item.role === 'paste').enabled, true);
});

test('read-only context menu only enables copy when text is selected', () => {
  const empty = buildContextMenuTemplate({ isEditable: false, selectionText: '' });
  const selected = buildContextMenuTemplate({ isEditable: false, selectionText: 'text' });
  assert.equal(empty[0].enabled, false);
  assert.equal(selected[0].enabled, true);
});
