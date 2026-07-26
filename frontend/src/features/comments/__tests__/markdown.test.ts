/**
 * 客户端 Markdown 预览测试(comment-inbox.md §4.1):marked 解析 + DOMPurify 净化。
 */
import { marked } from 'marked';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderMarkdownPreview } from '../markdown';

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
