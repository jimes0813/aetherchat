import { Fragment, type ReactNode } from 'react';


const safeLink = (value: string) => {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
};

function inline(text: string): ReactNode[] {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\))/g;
  return text.split(pattern).filter(Boolean).map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) {
      return <code key={index} className="rounded bg-[#eee6dc] px-1 py-0.5 font-mono text-[0.9em]">{part.slice(1, -1)}</code>;
    }
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link) {
      const href = safeLink(link[2]);
      return href
        ? <a key={index} href={href} target="_blank" rel="noreferrer noopener" className="text-[#7b4d24] underline underline-offset-2">{link[1]}</a>
        : <Fragment key={index}>{link[1]}</Fragment>;
    }
    return <Fragment key={index}>{part}</Fragment>;
  });
}

interface SafeMarkdownProps {
  content: string;
  className?: string;
}

export function SafeMarkdown({ content, className = '' }: SafeMarkdownProps) {
  const lines = String(content || '').replace(/\r\n?/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (line.startsWith('```')) {
      const language = line.slice(3).trim();
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith('```')) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push(
        <div key={`code-${index}`} className="my-3 overflow-hidden rounded-lg border border-[#d9d0c5] bg-[#292723]">
          {language && <div className="border-b border-white/10 px-3 py-1.5 text-[10px] uppercase tracking-wide text-[#cfc6ba]">{language}</div>}
          <pre className="overflow-auto p-3 text-xs leading-5 text-[#f4eee6]"><code>{code.join('\n')}</code></pre>
        </div>,
      );
      continue;
    }
    if (index + 1 < lines.length && line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) {
      const headers = line.replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(lines[index].replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim()));
        index += 1;
      }
      blocks.push(
        <div key={`table-${index}`} className="my-3 overflow-auto rounded-lg border border-[#ddd3c7]">
          <table className="min-w-full border-collapse text-left text-xs">
            <thead className="bg-[#eee6dc]"><tr>{headers.map((cell, cellIndex) => <th key={cellIndex} className="border-b border-[#d8cfc3] px-3 py-2 font-semibold">{inline(cell)}</th>)}</tr></thead>
            <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex} className="border-b border-[#eee7de] last:border-0">{headers.map((_, cellIndex) => <td key={cellIndex} className="px-3 py-2 align-top">{inline(row[cellIndex] || '')}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(<div key={`heading-${index}`} className={`${level === 1 ? 'text-lg' : level === 2 ? 'text-base' : 'text-sm'} mt-3 font-semibold`}>{inline(heading[2])}</div>);
      index += 1;
      continue;
    }
    if (/^>\s?/.test(line)) {
      const quote: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) quote.push(lines[index++].replace(/^>\s?/, ''));
      blocks.push(<blockquote key={`quote-${index}`} className="my-3 border-l-4 border-[#b98a5e] bg-[#f6eee4] px-3 py-2 text-[#625548]">{quote.map((item, itemIndex) => <div key={itemIndex}>{inline(item)}</div>)}</blockquote>);
      continue;
    }
    if (/^\s*(?:[-*+] |\d+\. )/.test(line)) {
      const ordered = /^\s*\d+\. /.test(line);
      const items: string[] = [];
      const matcher = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*+]\s+/;
      while (index < lines.length && matcher.test(lines[index])) items.push(lines[index++].replace(matcher, ''));
      const List = ordered ? 'ol' : 'ul';
      blocks.push(<List key={`list-${index}`} className={`my-2 space-y-1 pl-5 ${ordered ? 'list-decimal' : 'list-disc'}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{inline(item)}</li>)}</List>);
      continue;
    }
    const paragraph: string[] = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^(?:```|#{1,4}\s|>\s?|\s*(?:[-*+] |\d+\. ))/.test(lines[index])) {
      if (index + 1 < lines.length && lines[index].includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) break;
      paragraph.push(lines[index++]);
    }
    blocks.push(<p key={`paragraph-${index}`} className="my-2 whitespace-pre-wrap">{inline(paragraph.join('\n'))}</p>);
  }

  return <div className={`text-sm leading-7 text-[#4a4037] ${className}`}>{blocks}</div>;
}
