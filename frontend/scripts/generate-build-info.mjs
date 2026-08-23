import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(frontendRoot, '..');

function gitValue(args, fallback = 'unknown') {
  try {
    return execFileSync('git', args, {
      cwd: repositoryRoot,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim() || fallback;
  } catch {
    return fallback;
  }
}

const epoch = Number(process.env.SOURCE_DATE_EPOCH || 0);
const timestamp = epoch > 0
  ? new Date(epoch * 1000).toISOString()
  : new Date().toISOString();
const payload = {
  schema: 'aetherchat-build-info-v1',
  commit: process.env.AETHERCHAT_BUILD_COMMIT || gitValue(['rev-parse', 'HEAD']),
  dirty: gitValue(['status', '--porcelain'], '') !== '',
  timestamp,
  channel: process.env.AETHERCHAT_BUILD_CHANNEL || 'local-rc',
};

writeFileSync(
  resolve(frontendRoot, 'build-info.json'),
  `${JSON.stringify(payload, null, 2)}\n`,
  'utf8',
);
