import { useCallback } from 'react';
import { fetchJson, withApiAuth } from '../api/client';
import { API_BASE } from '../components/shared';
import type { GeneratedFile, GeneratedFileView } from '../types';

type GeneratedFileHandlers = {
  inspectGeneratedFile: (file: GeneratedFile) => void;
  resolveGeneratedFile: (file: GeneratedFile, includePreview?: boolean) => Promise<GeneratedFileView>;
  openGeneratedFile: (file: GeneratedFile, action: 'open' | 'reveal') => Promise<void>;
};

export function useGeneratedFiles(
  sessionId: string,
  onSelectArtifact: (path: string) => void,
  onOpenPanel: (panel: 'artifacts') => void,
  onNotice: (message: string) => void,
): GeneratedFileHandlers {
  const inspectGeneratedFile = useCallback((file: GeneratedFile) => {
    if (!file.path) return;
    onSelectArtifact(file.path);
    onOpenPanel('artifacts');
  }, [onOpenPanel, onSelectArtifact]);

  const resolveGeneratedFile = useCallback(async (
    file: GeneratedFile,
    includePreview = false,
  ): Promise<GeneratedFileView> => {
    if (!file.path) {
      return {
        location: file.url || null,
        preview_url: includePreview ? file.url || null : null,
        mime_type: file.mime_type,
      };
    }
    const encodedPath = file.path.split('/').map(encodeURIComponent).join('/');
    const location = await fetchJson<{
      path: string;
      mime_type?: string;
    }>(
      `${API_BASE}/artifacts/${encodedPath}/native-location?session_id=${encodeURIComponent(sessionId)}`,
    );
    const mimeType = location.mime_type || file.mime_type || 'application/octet-stream';
    const safeInlineMedia = (
      /^(image\/(?:png|jpeg|webp|gif)|video\/|audio\/)/i.test(mimeType)
    );
    if (!includePreview || !safeInlineMedia) {
      return { location: location.path, mime_type: mimeType };
    }
    const response = await fetch(
      `${API_BASE}/artifacts/${encodedPath}/content?session_id=${encodeURIComponent(sessionId)}`,
      withApiAuth(),
    );
    if (!response.ok) {
      throw new Error(`文件预览加载失败：HTTP ${response.status}`);
    }
    const blob = await response.blob();
    return {
      location: location.path,
      preview_url: URL.createObjectURL(blob),
      mime_type: mimeType,
    };
  }, [sessionId]);

  const openGeneratedFile = useCallback(async (
    file: GeneratedFile,
    action: 'open' | 'reveal',
  ) => {
    try {
      if (file.path && window.aetherRuntime?.openArtifact) {
        const opened = await window.aetherRuntime.openArtifact(
          sessionId,
          file.path,
          action,
        );
        onNotice(action === 'reveal'
          ? `已在文件夹中显示 ${opened.displayName}`
          : `已打开 ${opened.displayName}`);
        return;
      }
      if (action === 'open' && file.url && window.aetherRuntime?.openExternal) {
        await window.aetherRuntime.openExternal(file.url);
        onNotice(`已打开 ${file.name}`);
        return;
      }
      if (file.path) {
        inspectGeneratedFile(file);
        onNotice('浏览器模式无法调用系统文件管理器，已在工件面板中打开');
        return;
      }
      throw new Error('该文件没有可打开的本机路径');
    } catch (error) {
      onNotice(`打开文件失败：${String(error)}`);
    }
  }, [inspectGeneratedFile, onNotice, sessionId]);

  return { inspectGeneratedFile, resolveGeneratedFile, openGeneratedFile };
}
