import { useState } from 'react';
import { FileText, FolderOpen, Save } from 'lucide-react';
import { useI18n } from '../i18n';
import type { SelectedDesktopDocument } from './shared';

export function DesktopDocumentPanel({
  onStatus,
}: {
  onStatus: (status: string) => void;
}) {
  const { ui } = useI18n();
  const bridge = window.aetherDesktop;
  const [selected, setSelected] = useState<SelectedDesktopDocument | null>(null);
  const [editorText, setEditorText] = useState('');
  const [writeArmed, setWriteArmed] = useState(false);
  const [busy, setBusy] = useState('');

  const editorData = () => {
    if (!selected) throw new Error('请先选择文档');
    if (selected.document.editor_mode === 'text') return { text: editorText };
    const parsed = JSON.parse(editorText);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('结构化文档内容必须是 JSON 对象');
    }
    return parsed as Record<string, unknown>;
  };

  const selectDocument = async () => {
    if (!bridge) return;
    setBusy('select');
    try {
      const value = await bridge.selectDocument();
      if (!value) return;
      setSelected(value);
      setEditorText(value.document.editor_mode === 'text'
        ? String(value.document.data.text ?? '')
        : JSON.stringify(value.document.data, null, 2));
      setWriteArmed(false);
      onStatus(`已读取 ${value.displayName}（${value.document.format.toUpperCase()}）`);
    } catch (error) {
      onStatus(`读取失败：${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const saveDocument = async (saveAs: boolean) => {
    if (!bridge || !selected || !writeArmed) return;
    setBusy(saveAs ? 'save-as' : 'save');
    try {
      const data = editorData();
      if (saveAs) {
        const result = await bridge.saveDocumentAs(selected.handle, data);
        if (result) {
          setSelected({
            ...selected,
            handle: (result as { handle?: string }).handle || selected.handle,
            displayName: result.displayName,
          });
          const backup = result.backupCreated
            ? `；已备份原文件：${result.backupDisplayName || '备份已创建'}`
            : '';
          onStatus(`已另存为 ${result.displayName}${backup}`);
        }
      } else {
        const result = await bridge.saveDocument(selected.handle, data);
        const backup = result.backupCreated
          ? `；已创建备份：${result.backupDisplayName || '备份已创建'}`
          : '';
        onStatus(`文档保存成功：${result.displayName}${backup}`);
      }
      setWriteArmed(false);
    } catch (error) {
      onStatus(`保存失败：${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  return (
    <section className="mt-3 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold"><FileText size={15} />{ui('本地文档编辑')}</div>
          <div className="mt-1 text-xs text-[#625a52]">{ui('TXT/MD/JSON/CSV/TSV、DOCX、XLSX、PPTX，以及 PDF 元数据与附加备注。')}</div>
        </div>
        <button onClick={selectDocument} disabled={!bridge || busy === 'select'} className="rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs text-white disabled:opacity-40">
          {busy === 'select' ? ui('读取中') : <span className="flex items-center gap-1"><FolderOpen size={13} />{ui('选择文档')}</span>}
        </button>
      </div>
      {selected && (
        <div className="mt-3">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="font-semibold">{selected.displayName}</span>
            <span className="rounded bg-[#ebe3d8] px-2 py-1">{selected.document.format.toUpperCase()}</span>
            <span className="text-[#625a52]">{selected.document.size_bytes.toLocaleString()} bytes</span>
            {selected.document.truncated && <span className="text-[#9b3328]">{ui('大型表格仅加载前 500×100 单元格')}</span>}
          </div>
          {selected.document.read_only_text && (
            <details className="mb-2 rounded-lg border border-[#ded6ca] bg-white p-2">
              <summary className="cursor-pointer text-xs font-medium">{ui('查看 PDF 已提取文本（只读）')}</summary>
              <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-[#625a52]">{selected.document.read_only_text}</pre>
            </details>
          )}
          <textarea value={editorText} onChange={(event) => setEditorText(event.target.value)} aria-label={ui('本地文档编辑器')} className="h-64 w-full resize-y rounded-lg border border-[#ded6ca] bg-white p-3 font-mono text-xs leading-5 outline-none" />
          <label className="mt-2 flex items-center gap-2 text-xs text-[#625a52]">
            <input type="checkbox" checked={writeArmed} onChange={(event) => setWriteArmed(event.target.checked)} />
            {ui('我确认本次写入；覆盖保存（包括另存为覆盖已有目标）会先创建时间戳备份')}
          </label>
          <div className="mt-2 flex gap-2">
            <button onClick={() => saveDocument(false)} disabled={!writeArmed || Boolean(busy)} className="flex items-center gap-1 rounded-md bg-[#8d5f35] px-3 py-1.5 text-xs text-white disabled:opacity-40"><Save size={13} />{ui('覆盖保存')}</button>
            <button onClick={() => saveDocument(true)} disabled={!writeArmed || Boolean(busy)} className="rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-40">{ui('另存为')}</button>
          </div>
        </div>
      )}
    </section>
  );
}
