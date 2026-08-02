'use strict';

const AETHERCHAT_PROTOCOL = 'aetherchat:';
const MAX_PROMPT_LENGTH = 8000;
const SUPPORTED_ACTIONS = new Set(['chat', 'new']);
const SUPPORTED_MODES = new Set(['casual', 'roundtable', 'project']);

function normalizedPrompt(value) {
  return String(value || '')
    .replace(/\0/g, '')
    .replace(/\r\n?/g, '\n')
    .slice(0, MAX_PROMPT_LENGTH);
}

function parseLaunchUrl(value) {
  if (typeof value !== 'string' || !value.toLowerCase().startsWith('aetherchat://')) {
    return null;
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol.toLowerCase() !== AETHERCHAT_PROTOCOL) return null;
  const action = (parsed.hostname || parsed.pathname.replace(/^\/+/, '')).toLowerCase();
  if (!SUPPORTED_ACTIONS.has(action)) return null;
  const requestedMode = String(parsed.searchParams.get('mode') || '').toLowerCase();
  return {
    action: 'compose',
    prompt: normalizedPrompt(parsed.searchParams.get('prompt')),
    mode: SUPPORTED_MODES.has(requestedMode) ? requestedMode : null,
    newConversation: action === 'new',
    source: 'protocol',
  };
}

function launchIntentFromArgv(argv) {
  if (!Array.isArray(argv)) return null;
  for (const argument of argv) {
    const intent = parseLaunchUrl(argument);
    if (intent) return intent;
  }
  return null;
}

function quickEntryIntent() {
  return {
    action: 'compose',
    prompt: '',
    mode: null,
    newConversation: false,
    source: 'shortcut',
  };
}

module.exports = {
  AETHERCHAT_PROTOCOL,
  MAX_PROMPT_LENGTH,
  launchIntentFromArgv,
  parseLaunchUrl,
  quickEntryIntent,
};
