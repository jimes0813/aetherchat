'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {
  MAX_PROMPT_LENGTH,
  launchIntentFromArgv,
  parseLaunchUrl,
  quickEntryIntent,
} = require('./launch-intent.cjs');

test('parses safe AetherChat chat and new-conversation deep links', () => {
  assert.deepEqual(
    parseLaunchUrl('aetherchat://chat?prompt=%E5%88%86%E6%9E%90%E8%BF%99%E4%BB%BD%E6%8A%A5%E5%91%8A&mode=project'),
    {
      action: 'compose',
      prompt: '分析这份报告',
      mode: 'project',
      newConversation: false,
      source: 'protocol',
    },
  );
  assert.equal(parseLaunchUrl('aetherchat://new?prompt=hello')?.newConversation, true);
});

test('rejects foreign or unsupported launch URLs and normalizes input', () => {
  assert.equal(parseLaunchUrl('https://example.com/?prompt=secret'), null);
  assert.equal(parseLaunchUrl('aetherchat://settings?prompt=hello'), null);
  assert.equal(parseLaunchUrl('not a url'), null);
  const parsed = parseLaunchUrl(`aetherchat://chat?prompt=${encodeURIComponent(`a\0b${'x'.repeat(MAX_PROMPT_LENGTH + 5)}`)}&mode=unsafe`);
  assert.equal(parsed.mode, null);
  assert.equal(parsed.prompt.includes('\0'), false);
  assert.equal(parsed.prompt.length, MAX_PROMPT_LENGTH);
});

test('finds a deep link in process arguments and creates a shortcut intent', () => {
  assert.equal(
    launchIntentFromArgv(['/opt/AetherChat/aetherchat-frontend', '--flag', 'aetherchat://new?mode=casual'])?.mode,
    'casual',
  );
  assert.equal(launchIntentFromArgv(['aetherchat://unknown']), null);
  assert.deepEqual(quickEntryIntent(), {
    action: 'compose',
    prompt: '',
    mode: null,
    newConversation: false,
    source: 'shortcut',
  });
});
