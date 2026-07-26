/**
 * 文件类型图标(mime/扩展名 → 字形)。无图标库、无 emoji:内联 SVG 路径,
 * 颜色经 currentColor(由父级语义 token 控制),aria-hidden(读屏由文件名承载)。
 */

export interface FileIconProps {
  readonly mimeType: string | null;
  readonly extension: string | null;
  readonly isImage: boolean;
  readonly className?: string;
}

type GlyphKind = 'image' | 'pdf' | 'text' | 'archive' | 'sheet' | 'doc' | 'generic';

function resolveGlyph(mimeType: string | null, extension: string | null, isImage: boolean): GlyphKind {
  if (isImage) return 'image';
  const mime = (mimeType ?? '').toLowerCase();
  const ext = (extension ?? '').toLowerCase();
  if (mime === 'application/pdf' || ext === 'pdf') return 'pdf';
  if (mime.includes('zip') || mime.includes('gzip') || ext === 'zip' || ext === 'gz') {
    return 'archive';
  }
  if (mime.includes('spreadsheet') || ext === 'xlsx' || ext === 'csv') return 'sheet';
  if (mime.includes('wordprocessing') || ext === 'docx') return 'doc';
  if (mime.startsWith('text/') || ['txt', 'log', 'md'].includes(ext)) return 'text';
  return 'generic';
}

/** 基础文件轮廓 + 按类型叠加的笔画;viewBox 24×24,描边随 currentColor。 */
const GLYPH_PATHS: Record<GlyphKind, React.JSX.Element> = {
  image: (
    <>
      <circle cx="9" cy="9.5" r="1.6" />
      <path d="M5 17l4-4 3 3 3.5-4.5L19 17" />
    </>
  ),
  pdf: <path d="M8 12h8M8 15h5" />,
  text: <path d="M8 11h8M8 14h8M8 17h5" />,
  archive: <path d="M11 8h2M11 11h2M11 14h2v3h-2z" />,
  sheet: <path d="M8 11h8v6H8zM8 14h8M12 11v6" />,
  doc: <path d="M8 11h8M8 14h8M8 17h8" />,
  generic: <path d="M8 12h8M8 15h5" />,
};

export function FileIcon(props: FileIconProps): React.JSX.Element {
  const kind = resolveGlyph(props.mimeType, props.extension, props.isImage);
  return (
    <svg
      className={props.className}
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M6 3h8l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M14 3v4h4" />
      {GLYPH_PATHS[kind]}
    </svg>
  );
}
