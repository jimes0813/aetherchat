import type { GeneratedFile } from '../types';

const UUID_PREFIX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[_-]?/i;

export function isTechnicalGeneratedFile(file: GeneratedFile): boolean {
  const name = String(file.name || file.path || '').toLowerCase();
  return name.endsWith('.metadata.json') || name.endsWith('.metadata.jsonl');
}

export function generatedFileBasename(file: GeneratedFile): string {
  const raw = String(file.name || file.path || file.url || 'file').replace(/\\/g, '/');
  const base = raw.split('/').pop() || raw;
  return base;
}

export function displayGeneratedFileName(file: GeneratedFile): string {
  const base = generatedFileBasename(file);
  const stripped = base.replace(UUID_PREFIX, '');
  return stripped || base;
}

export function visibleGeneratedFiles(files: GeneratedFile[] | undefined): GeneratedFile[] {
  return (files || []).filter((file) => !isTechnicalGeneratedFile(file));
}

export function technicalGeneratedFiles(files: GeneratedFile[] | undefined): GeneratedFile[] {
  return (files || []).filter(isTechnicalGeneratedFile);
}
