import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SafeMarkdown } from './SafeMarkdown';

describe('SafeMarkdown 安全渲染', () => {
  it('拒绝 javascript: 协议链接，仅保留纯文本标签', () => {
    const { container } = render(
      <SafeMarkdown content={'[点我](javascript:alert(1))'} />,
    );
    expect(container.querySelector('a')).toBeNull();
    expect(container).toHaveTextContent('点我');
    expect(container.innerHTML).not.toContain('javascript:');
  });

  it('拒绝 data: URL 链接', () => {
    const { container } = render(
      <SafeMarkdown content={'[下载](data:text/html;base64,PHNjcmlwdD4=)'} />,
    );
    expect(container.querySelector('a')).toBeNull();
    expect(container).toHaveTextContent('下载');
  });

  it('拒绝 vbscript:/file: 等其他危险协议链接', () => {
    const { container } = render(
      <SafeMarkdown content={'[a](vbscript:msgbox(1)) [b](file:///etc/passwd)'} />,
    );
    expect(container.querySelectorAll('a')).toHaveLength(0);
    expect(container).toHaveTextContent('a');
    expect(container).toHaveTextContent('b');
  });

  it('原始 HTML 注入被转义，不产生真实元素', () => {
    const { container } = render(
      <SafeMarkdown content={'<script>alert(1)</script><img src=x onerror=alert(1)>'} />,
    );
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    // 作为纯文本可见
    expect(container).toHaveTextContent('<script>alert(1)</script>');
  });

  it('嵌套标记不崩溃且不会生成危险结构', () => {
    const { container } = render(
      <SafeMarkdown content={'**加粗 `code` 文本** 与 `[嵌 **套**](https://example.com)`'} />,
    );
    expect(container.querySelector('script')).toBeNull();
    const link = container.querySelector('a');
    if (link) {
      expect(link.href).toMatch(/^https?:\/\//);
      expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    }
    expect(container.querySelector('strong')).not.toBeNull();
  });

  it('超长输入可正常渲染且不含可执行内容', () => {
    const huge = `# 标题\n\n${'普通段落文字。'.repeat(20000)}\n\n- 列表项\n`;
    const { container } = render(<SafeMarkdown content={huge} />);
    expect(container.querySelector('script')).toBeNull();
    expect(container.textContent!.length).toBeGreaterThan(100000);
  });

  it('允许 http/https 链接并附加安全属性', () => {
    render(<SafeMarkdown content={'[官网](https://example.com/path?q=1)'} />);
    const link = screen.getByRole('link', { name: '官网' });
    expect(link).toHaveAttribute('href', 'https://example.com/path?q=1');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noreferrer'));
  });
});

describe('SafeMarkdown 正常 Markdown 渲染', () => {
  it('渲染 1-4 级标题', () => {
    const { container } = render(
      <SafeMarkdown content={'# 一级\n\n## 二级\n\n### 三级\n\n#### 四级'} />,
    );
    expect(container).toHaveTextContent('一级');
    expect(container).toHaveTextContent('四级');
    expect(container.querySelector('.text-lg')).toHaveTextContent('一级');
    expect(container.querySelector('.text-base')).toHaveTextContent('二级');
  });

  it('渲染无序与有序列表', () => {
    const { container } = render(
      <SafeMarkdown content={'- 苹果\n- 香蕉\n\n1. 第一步\n2. 第二步'} />,
    );
    const ul = container.querySelector('ul');
    const ol = container.querySelector('ol');
    expect(ul).not.toBeNull();
    expect(ul!.querySelectorAll('li')).toHaveLength(2);
    expect(ol).not.toBeNull();
    expect(ol!.querySelectorAll('li')).toHaveLength(2);
    expect(ul).toHaveTextContent('苹果');
    expect(ol).toHaveTextContent('第二步');
  });

  it('渲染围栏代码块并保留语言标记', () => {
    const { container } = render(
      <SafeMarkdown content={'```ts\nconst a = 1 < 2 && "x";\n```'} />,
    );
    const pre = container.querySelector('pre code');
    expect(pre).toHaveTextContent('const a = 1 < 2 && "x";');
    expect(container).toHaveTextContent('ts');
    // 代码块中的 HTML 尖括号不被解析为元素
    expect(container.querySelectorAll('pre script')).toHaveLength(0);
  });

  it('渲染行内代码与加粗', () => {
    const { container } = render(
      <SafeMarkdown content={'这是 `inline` 和 **重点** 文本'} />,
    );
    expect(container.querySelector('code')).toHaveTextContent('inline');
    expect(container.querySelector('strong')).toHaveTextContent('重点');
  });

  it('渲染引用块与表格', () => {
    const { container } = render(
      <SafeMarkdown content={'> 引用内容\n\n| 名称 | 值 |\n| --- | --- |\n| a | 1 |'} />,
    );
    expect(container.querySelector('blockquote')).toHaveTextContent('引用内容');
    const table = container.querySelector('table');
    expect(table).not.toBeNull();
    expect(table!.querySelectorAll('th')).toHaveLength(2);
    expect(table!.querySelectorAll('td')).toHaveLength(2);
    expect(table).toHaveTextContent('名称');
  });

  it('空内容与非法输入不抛异常', () => {
    const { container } = render(<SafeMarkdown content={''} />);
    expect(container.firstChild).not.toBeNull();
    expect(() => render(<SafeMarkdown content={undefined as unknown as string} />)).not.toThrow();
  });
});
