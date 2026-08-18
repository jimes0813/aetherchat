const LEGACY_AUDIO_PERMISSIONS = new Set(['microphone', 'audioCapture']);

function requestedMediaTypes(details = {}) {
  const types = [];
  if (Array.isArray(details.mediaTypes)) types.push(...details.mediaTypes);
  if (details.mediaType) types.push(details.mediaType);
  return types.map((value) => String(value || '').toLowerCase());
}

function isAllowedMediaPermission(permission, details = {}) {
  const requested = String(permission || '');
  if (LEGACY_AUDIO_PERMISSIONS.has(requested)) return true;
  if (requested !== 'media') return false;

  const types = requestedMediaTypes(details);
  // Electron 31+ often asks for "media" without mediaTypes on the check path.
  // Treat that as microphone capture; only refuse when video/screen is explicit.
  if (types.length === 0) return true;
  return types.every((mediaType) => mediaType === 'audio' || mediaType === 'microphone' || mediaType === 'audiocapture');
}

function createMediaPermissionHandlers(isTrustedWebContents = () => true) {
  return {
    requestHandler(webContents, permission, callback, details) {
      callback(Boolean(isTrustedWebContents(webContents)) && isAllowedMediaPermission(permission, details));
    },
    checkHandler(webContents, permission, _requestingOrigin, details) {
      return Boolean(isTrustedWebContents(webContents)) && isAllowedMediaPermission(permission, details);
    },
  };
}

module.exports = {
  LEGACY_AUDIO_PERMISSIONS,
  requestedMediaTypes,
  isAllowedMediaPermission,
  createMediaPermissionHandlers,
};
