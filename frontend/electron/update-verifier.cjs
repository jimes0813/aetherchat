const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const MAX_UPDATE_BYTES = 2 * 1024 * 1024 * 1024;
const MAX_MANIFEST_BYTES = 1024 * 1024;

function codedError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function writeAllSync(descriptor, bytes, writeImpl = fs.writeSync) {
  let offset = 0;
  while (offset < bytes.length) {
    const written = writeImpl(descriptor, bytes, offset, bytes.length - offset);
    if (!Number.isInteger(written) || written <= 0) {
      throw new Error('Update package could not be written completely');
    }
    offset += written;
  }
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function parseVersion(value) {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/.exec(String(value || ''));
  if (!match) throw new Error('Update manifest contains an invalid semantic version');
  return { numbers: match.slice(1, 4).map(Number), prerelease: match[4] || '' };
}

function compareVersions(left, right) {
  const a = parseVersion(left);
  const b = parseVersion(right);
  for (let index = 0; index < 3; index += 1) {
    if (a.numbers[index] !== b.numbers[index]) return a.numbers[index] > b.numbers[index] ? 1 : -1;
  }
  if (a.prerelease === b.prerelease) return 0;
  if (!a.prerelease) return 1;
  if (!b.prerelease) return -1;
  const aParts = a.prerelease.split('.');
  const bParts = b.prerelease.split('.');
  for (let index = 0; index < Math.max(aParts.length, bParts.length); index += 1) {
    if (aParts[index] === undefined) return -1;
    if (bParts[index] === undefined) return 1;
    if (aParts[index] === bParts[index]) continue;
    const aNumeric = /^\d+$/.test(aParts[index]);
    const bNumeric = /^\d+$/.test(bParts[index]);
    if (aNumeric && bNumeric) return Number(aParts[index]) > Number(bParts[index]) ? 1 : -1;
    if (aNumeric !== bNumeric) return aNumeric ? -1 : 1;
    return aParts[index] > bParts[index] ? 1 : -1;
  }
  return 0;
}

function assertHttpsUrl(value, label) {
  const url = new URL(String(value || ''));
  if (url.protocol !== 'https:' || url.username || url.password) {
    throw new Error(`${label} must use credential-free HTTPS`);
  }
  return url;
}

function verifyManifestEnvelope(envelope, options) {
  if (!envelope || typeof envelope !== 'object' || !envelope.manifest || !envelope.signature) {
    throw new Error('Signed update envelope is incomplete');
  }
  if (envelope.signature.algorithm !== 'Ed25519' || typeof envelope.signature.value !== 'string') {
    throw new Error('Update manifest signature is missing or unsupported');
  }
  if (envelope.manifest.schema !== 'aetherchat-update-v1' || !Array.isArray(envelope.manifest.releases)) {
    throw new Error('Update manifest schema is unsupported');
  }
  const signature = Buffer.from(envelope.signature.value, 'base64');
  if (!signature.length || !crypto.verify(
    null,
    Buffer.from(canonicalJson(envelope.manifest), 'utf8'),
    options.publicKey,
    signature,
  )) {
    throw new Error('Update manifest signature verification failed');
  }
  if (compareVersions(envelope.manifest.version, options.currentVersion) <= 0) {
    throw codedError('UPDATE_NOT_AVAILABLE', 'Update version is not newer than the installed version');
  }
  const release = envelope.manifest.releases.find((item) => (
    item && item.platform === options.platform && item.arch === options.arch
  ));
  if (!release) throw new Error('Update manifest has no package for this platform and architecture');
  if (!/^[a-f0-9]{64}$/.test(String(release.sha256 || ''))) throw new Error('Update package SHA-256 is invalid');
  if (!Number.isSafeInteger(release.size) || release.size <= 0 || release.size > MAX_UPDATE_BYTES) {
    throw new Error('Update package size is invalid or exceeds the 2 GB limit');
  }
  const url = assertHttpsUrl(release.url, 'Update package URL');
  const filename = path.basename(url.pathname);
  if (!filename || filename === '.' || filename === '..' || !/^[A-Za-z0-9._-]+$/.test(filename)) {
    throw new Error('Update package filename is unsafe');
  }
  return { manifest: envelope.manifest, release: { ...release, url: url.toString() }, filename };
}

async function stageUpdate({ manifestUrl, publicKey, currentVersion, platform, arch, downloadDir, fetchImpl = fetch }) {
  const checkedManifestUrl = assertHttpsUrl(manifestUrl, 'Update manifest URL');
  const manifestResponse = await fetchImpl(checkedManifestUrl, { redirect: 'error', cache: 'no-store' });
  if (!manifestResponse.ok) throw new Error(`Update manifest request failed (HTTP ${manifestResponse.status})`);
  const manifestBytes = Buffer.from(await manifestResponse.arrayBuffer());
  if (!manifestBytes.length || manifestBytes.length > MAX_MANIFEST_BYTES) throw new Error('Update manifest is empty or exceeds the 1 MB limit');
  let envelope;
  try {
    envelope = JSON.parse(manifestBytes.toString('utf8'));
  } catch {
    throw new Error('Update manifest is not valid JSON');
  }
  const verified = verifyManifestEnvelope(envelope, { publicKey, currentVersion, platform, arch });
  const packageResponse = await fetchImpl(verified.release.url, { redirect: 'error', cache: 'no-store' });
  if (!packageResponse.ok) throw new Error(`Update package request failed (HTTP ${packageResponse.status})`);
  const declaredLength = Number(packageResponse.headers.get('content-length') || 0);
  if (declaredLength && declaredLength !== verified.release.size) throw new Error('Update package Content-Length does not match the signed manifest');
  fs.mkdirSync(downloadDir, { recursive: true, mode: 0o700 });
  const finalPath = path.join(downloadDir, verified.filename);
  const partialPath = `${finalPath}.${crypto.randomBytes(8).toString('hex')}.partial`;
  const hash = crypto.createHash('sha256');
  let received = 0;
  let descriptor;
  try {
    fs.rmSync(finalPath, { force: true });
    descriptor = fs.openSync(partialPath, 'wx', 0o600);
    if (!packageResponse.body) throw new Error('Update package response has no body');
    for await (const chunk of packageResponse.body) {
      const bytes = Buffer.from(chunk);
      received += bytes.length;
      if (received > verified.release.size || received > MAX_UPDATE_BYTES) throw new Error('Update package exceeds the signed size');
      hash.update(bytes);
      writeAllSync(descriptor, bytes);
    }
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor);
    descriptor = undefined;
    if (received !== verified.release.size) throw new Error('Update package size does not match the signed manifest');
    const digest = hash.digest('hex');
    if (!crypto.timingSafeEqual(Buffer.from(digest, 'hex'), Buffer.from(verified.release.sha256, 'hex'))) {
      throw new Error('Update package SHA-256 verification failed');
    }
    fs.renameSync(partialPath, finalPath);
    return { ...verified, path: finalPath, sha256: digest };
  } catch (error) {
    if (descriptor !== undefined) {
      try { fs.closeSync(descriptor); } catch {}
    }
    try { fs.unlinkSync(partialPath); } catch {}
    throw error;
  }
}

module.exports = {
  MAX_UPDATE_BYTES,
  MAX_MANIFEST_BYTES,
  canonicalJson,
  compareVersions,
  codedError,
  writeAllSync,
  verifyManifestEnvelope,
  stageUpdate,
};
