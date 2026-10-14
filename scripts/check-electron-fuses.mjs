#!/usr/bin/env node
/**
 * CI gate: fail if Electron fuses are not wired into the frontend build.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pkgPath = path.join(root, 'frontend', 'package.json');
const hookPath = path.join(root, 'frontend', 'scripts', 'after-pack-fuses.cjs');

const failures = [];

if (!fs.existsSync(pkgPath)) {
  failures.push(`missing ${pkgPath}`);
} else {
  const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
  const afterPack = pkg.build?.afterPack;
  if (afterPack !== './scripts/after-pack-fuses.cjs' && afterPack !== 'scripts/after-pack-fuses.cjs') {
    failures.push(`frontend/package.json build.afterPack must be ./scripts/after-pack-fuses.cjs (got ${JSON.stringify(afterPack)})`);
  }
  const deps = { ...pkg.dependencies, ...pkg.devDependencies };
  if (!deps['@electron/fuses']) {
    failures.push('frontend/package.json must declare @electron/fuses');
  }
}

if (!fs.existsSync(hookPath)) {
  failures.push(`missing ${hookPath}`);
} else {
  const body = fs.readFileSync(hookPath, 'utf8');
  for (const needle of [
    'EnableNodeCliInspectArguments',
    'EnableNodeOptionsEnvironmentVariable',
    'RunAsNode',
    'flipFuses',
  ]) {
    if (!body.includes(needle)) {
      failures.push(`after-pack-fuses.cjs missing required fuse wiring: ${needle}`);
    }
  }
}

if (failures.length) {
  console.error('check-electron-fuses failed:');
  for (const item of failures) console.error(` - ${item}`);
  process.exit(1);
}

console.log('check-electron-fuses: ok');
