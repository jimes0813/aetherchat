const test = require('node:test');
const assert = require('node:assert/strict');
const {
  isAllowedMediaPermission,
  createMediaPermissionHandlers,
} = require('./media-permission.cjs');

test('allows only microphone capture permissions', () => {
  assert.equal(isAllowedMediaPermission('media', { mediaTypes: ['audio'] }), true);
  assert.equal(isAllowedMediaPermission('media', { mediaType: 'audio' }), true);
  assert.equal(isAllowedMediaPermission('media'), true);
  assert.equal(isAllowedMediaPermission('media', { mediaTypes: ['video'] }), false);
  assert.equal(isAllowedMediaPermission('media', { mediaTypes: ['audio', 'video'] }), false);
  assert.equal(isAllowedMediaPermission('microphone'), true);
  assert.equal(isAllowedMediaPermission('audioCapture'), true);
  assert.equal(isAllowedMediaPermission('camera'), false);
  assert.equal(isAllowedMediaPermission('display-capture'), false);
  assert.equal(isAllowedMediaPermission('geolocation'), false);
  assert.equal(isAllowedMediaPermission('notifications'), false);
});

test('permission handlers keep the allowlist tight', () => {
  const trusted = {};
  const { requestHandler, checkHandler } = createMediaPermissionHandlers((webContents) => webContents === trusted);
  let allowed;
  requestHandler(trusted, 'media', (value) => { allowed = value; }, { mediaTypes: ['audio'] });
  assert.equal(allowed, true);
  requestHandler(trusted, 'media', (value) => { allowed = value; }, { mediaTypes: ['video'] });
  assert.equal(allowed, false);
  requestHandler({}, 'media', (value) => { allowed = value; }, { mediaTypes: ['audio'] });
  assert.equal(allowed, false);
  assert.equal(checkHandler(trusted, 'media', 'file://', { mediaType: 'audio' }), true);
  assert.equal(checkHandler(trusted, 'media', 'file://', { mediaType: 'video' }), false);
  assert.equal(checkHandler(trusted, 'display-capture', 'file://', {}), false);
});
