const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const {
  canonicalJson,
  compareVersions,
  stageUpdate,
  verifyManifestEnvelope,
  writeAllSync,
} = require('./update-verifier.cjs');

const keys = crypto.generateKeyPairSync('ed25519');
const payload = Buffer.from('signed aetherchat update payload');
const release = {
  platform: 'linux', arch: 'arm64', url: 'https://updates.example.test/AetherChat.AppImage',
  size: payload.length, sha256: crypto.createHash('sha256').update(payload).digest('hex'),
};

function envelope(overrides = {}) {
  const manifest = {
    schema: 'aetherchat-update-v1', version: '0.3.0', published_at: '2026-07-16T00:00:00Z',
    releases: [release], ...overrides,
  };
  return {
    manifest,
    signature: { algorithm: 'Ed25519', value: crypto.sign(null, Buffer.from(canonicalJson(manifest)), keys.privateKey).toString('base64') },
  };
}

test('accepts a correctly signed update for the current platform', () => {
  const result = verifyManifestEnvelope(envelope(), {
    publicKey: keys.publicKey, currentVersion: '0.2.0', platform: 'linux', arch: 'arm64',
  });
  assert.equal(result.release.sha256, release.sha256);
});

test('compares numeric prerelease identifiers using SemVer ordering', () => {
  assert.equal(compareVersions('0.2.1-rc.10', '0.2.1-rc.9'), 1);
  assert.equal(compareVersions('0.2.1-rc.9', '0.2.1-rc.10'), -1);
  assert.equal(compareVersions('0.2.1', '0.2.1-rc.10'), 1);
  assert.equal(compareVersions('0.2.1-rc.10', '0.2.1-rc.10'), 0);
});

test('writes every byte even when the file API returns short writes', () => {
  const payload = Buffer.from('complete update package');
  const chunks = [];
  const writeImpl = (_descriptor, bytes, offset, length) => {
    const written = Math.min(3, length);
    chunks.push(Buffer.from(bytes.subarray(offset, offset + written)));
    return written;
  };
  writeAllSync(123, payload, writeImpl);
  assert.deepEqual(Buffer.concat(chunks), payload);
});

test('rejects missing/bad signatures, downgrade, wrong architecture and unsafe URLs', () => {
  const base = { publicKey: keys.publicKey, currentVersion: '0.2.0', platform: 'linux', arch: 'arm64' };
  assert.throws(() => verifyManifestEnvelope({ manifest: envelope().manifest }, base), /incomplete/);
  const tampered = envelope();
  tampered.manifest.version = '9.9.9';
  assert.throws(() => verifyManifestEnvelope(tampered, base), /signature verification failed/);
  assert.throws(() => verifyManifestEnvelope(envelope({ version: '0.1.9' }), base), /not newer/);
  assert.throws(() => verifyManifestEnvelope(envelope(), { ...base, arch: 'x64' }), /no package/);
  assert.throws(() => verifyManifestEnvelope(envelope({ releases: [{ ...release, url: 'http://updates.example.test/file' }] }), base), /credential-free HTTPS/);
});

test('stages only a hash-matching package and leaves no partial file after rejection', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'aetherchat-update-test-'));
  const signed = envelope();
  fs.writeFileSync(path.join(directory, 'AetherChat.AppImage'), 'stale partial retry');
  const fetchOk = async (url) => String(url).endsWith('.AppImage')
    ? new Response(payload, { status: 200, headers: { 'content-length': String(payload.length) } })
    : new Response(JSON.stringify(signed), { status: 200, headers: { 'content-type': 'application/json' } });
  const staged = await stageUpdate({
    manifestUrl: 'https://updates.example.test/latest.json', publicKey: keys.publicKey,
    currentVersion: '0.2.0', platform: 'linux', arch: 'arm64', downloadDir: directory, fetchImpl: fetchOk,
  });
  assert.deepEqual(fs.readFileSync(staged.path), payload);

  const badPayload = Buffer.from('tampered');
  const fetchBad = async (url) => String(url).endsWith('.AppImage')
    ? new Response(badPayload, { status: 200 })
    : new Response(JSON.stringify(signed), { status: 200 });
  await assert.rejects(stageUpdate({
    manifestUrl: 'https://updates.example.test/latest.json', publicKey: keys.publicKey,
    currentVersion: '0.2.0', platform: 'linux', arch: 'arm64', downloadDir: directory, fetchImpl: fetchBad,
  }), /size does not match/);
  assert.equal(fs.readdirSync(directory).some((name) => name.endsWith('.partial')), false);
  fs.rmSync(directory, { recursive: true, force: true });
});
