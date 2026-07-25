/**
 * WCAG 2.1 对比度计算 —— 供语义 token 的 AA 自证测试使用(README §6.12)。
 * 公式来源:WCAG 2.1 relative luminance / contrast ratio 定义。
 */

/** WCAG 2.1 AA 正文文本对比度阈值 */
export const WCAG_AA_RATIO = 4.5;

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

/**
 * 解析 `#rgb` / `#rrggbb`(大小写不敏感)为 0–255 通道值。
 * @throws 非法十六进制颜色串抛错。
 */
export function hexToRgb(hex: string): Rgb {
  const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!match) {
    throw new Error(`Invalid hex color: ${JSON.stringify(hex)}`);
  }
  const raw = match[1];
  const expanded = raw.length === 3 ? raw.replace(/./g, (char) => char + char) : raw;
  const value = Number.parseInt(expanded, 16);
  return { r: (value >> 16) & 0xff, g: (value >> 8) & 0xff, b: value & 0xff };
}

/** WCAG 2.1 相对亮度(0 = 最暗,1 = 最亮)。 */
export function relativeLuminance(rgb: Rgb): number {
  const linearize = (channel: number): number => {
    const srgb = channel / 255;
    return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
  };
  const r = linearize(rgb.r);
  const g = linearize(rgb.g);
  const b = linearize(rgb.b);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 两个十六进制颜色之间的 WCAG 对比度比(1–21,与参数顺序无关)。 */
export function contrastRatio(fg: string, bg: string): number {
  const l1 = relativeLuminance(hexToRgb(fg));
  const l2 = relativeLuminance(hexToRgb(bg));
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

/** 是否满足 WCAG 2.1 AA 正文文本对比度(≥ 4.5:1)。 */
export function meetsAA(fg: string, bg: string): boolean {
  return contrastRatio(fg, bg) >= WCAG_AA_RATIO;
}
