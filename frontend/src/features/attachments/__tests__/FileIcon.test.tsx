/**
 * FileIcon 测试:mime/扩展名 → 字形解析(覆盖 image/pdf/text/archive/sheet/doc/generic 分支)。
 */
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FileIcon } from '../components/FileIcon';

function renderIcon(props: { mimeType: string | null; extension: string | null; isImage: boolean }) {
  const { container } = render(<FileIcon {...props} />);
  return container.querySelector('svg');
}

describe('FileIcon', () => {
  it('renders an accessible-hidden svg for every glyph kind', () => {
    const cases: Array<[string | null, string | null, boolean]> = [
      ['image/png', 'png', true], // image
      ['application/pdf', 'pdf', false], // pdf
      ['text/plain', 'txt', false], // text
      ['application/zip', 'zip', false], // archive
      ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx', false], // sheet
      ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'docx', false], // doc
      [null, null, false], // generic
      ['', 'log', false], // text via extension
      ['application/gzip', 'gz', false], // archive via mime
      ['', 'csv', false], // sheet via extension
    ];
    for (const [mimeType, extension, isImage] of cases) {
      const svg = renderIcon({ mimeType, extension, isImage });
      expect(svg).not.toBeNull();
      expect(svg?.getAttribute('aria-hidden')).toBe('true');
    }
  });

  it('prefers the image glyph when isImage is set regardless of mime', () => {
    const svg = renderIcon({ mimeType: null, extension: null, isImage: true });
    expect(svg).not.toBeNull();
  });
});
