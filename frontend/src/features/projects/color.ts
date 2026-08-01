const PROJECT_COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;

/**
 * 旧数据也必须经过同一白名单。非法值返回 null，调用方不创建内联样式，
 * 从而不会把历史 CSS 值交给浏览器解析。
 */
export function safeProjectColor(value: string | null): string | null {
  return typeof value === 'string' && PROJECT_COLOR_PATTERN.test(value) ? value : null;
}
