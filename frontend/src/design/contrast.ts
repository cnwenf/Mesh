/**
 * WCAG 2.1 对比度计算 —— 供语义 token 的 AA 自证测试与 CI 对比度关卡使用(README §6.12)。
 * 公式来源:WCAG 2.1 relative luminance / contrast ratio 定义。
 */

/** WCAG 2.1 AA 正文文本对比度阈值 */
export const WCAG_AA_RATIO = 4.5;

/** WCAG 2.1 AA 大文本(≥24px,或 ≥18.66px 且加粗)与图形元件对比度阈值 */
export const WCAG_AA_LARGE_RATIO = 3.0;

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

/** 含 alpha 通道的颜色(0 = 全透明,1 = 不透明)。 */
export interface Rgba extends Rgb {
  a: number;
}

/** 对比度配对用途:text = 正文;large-text = 大文本;graphic = 图形元件。 */
export type ContrastKind = 'text' | 'large-text' | 'graphic';

const HEX_PATTERN = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;
const RGB_PATTERN =
  /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(0|1|0?\.\d+)\s*)?\)$/;
const OKLCH_PATTERN =
  /^oklch\(\s*((?:\d+(?:\.\d+)?|\.\d+)%?)\s+((?:\d+(?:\.\d+)?|\.\d+))\s+(-?(?:\d+(?:\.\d+)?|\.\d+))(?:deg)?\s*(?:\/\s*((?:\d+(?:\.\d+)?|\.\d+)%?))?\s*\)$/i;
const MAX_CHANNEL = 255;

function parseUnitInterval(value: string): number | null {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed)) return null;
  const normalized = value.endsWith('%') ? parsed / 100 : parsed;
  return normalized >= 0 && normalized <= 1 ? normalized : null;
}

/** CSS Color 4 OKLCH → clipped sRGB, using the published Oklab matrices. */
function oklchToRgb(lightness: number, chroma: number, hue: number): Rgb {
  const radians = (hue * Math.PI) / 180;
  const a = chroma * Math.cos(radians);
  const b = chroma * Math.sin(radians);
  const lRoot = lightness + 0.3963377774 * a + 0.2158037573 * b;
  const mRoot = lightness - 0.1055613458 * a - 0.0638541728 * b;
  const sRoot = lightness - 0.0894841775 * a - 1.291485548 * b;
  const l = lRoot ** 3;
  const m = mRoot ** 3;
  const s = sRoot ** 3;
  const linear = {
    r: 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    g: -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    b: -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  };
  const encode = (channel: number): number => {
    const clipped = Math.min(1, Math.max(0, channel));
    const srgb = clipped <= 0.0031308 ? clipped * 12.92 : 1.055 * clipped ** (1 / 2.4) - 0.055;
    return Math.round(srgb * MAX_CHANNEL);
  };
  return { r: encode(linear.r), g: encode(linear.g), b: encode(linear.b) };
}

/**
 * 解析 `#rgb` / `#rrggbb`(大小写不敏感)为 0–255 通道值。
 * @throws 非法十六进制颜色串抛错。
 */
export function hexToRgb(hex: string): Rgb {
  const match = HEX_PATTERN.exec(hex.trim());
  if (!match) {
    throw new Error(`Invalid hex color: ${JSON.stringify(hex)}`);
  }
  const raw = match[1];
  const expanded = raw.length === 3 ? raw.replace(/./g, (char) => char + char) : raw;
  const value = Number.parseInt(expanded, 16);
  return { r: (value >> 16) & 0xff, g: (value >> 8) & 0xff, b: value & 0xff };
}

/**
 * 解析 `#rgb` / `#rrggbb` / `rgb()` / `rgba()` / `oklch()` 为 RGBA 通道(alpha 缺省 1)。
 * 非法输入返回 `null`(不抛错)—— 供门禁脚本对任意 CSS 值安全探测。
 */
export function parseColor(value: string): Rgba | null {
  const trimmed = value.trim();
  if (HEX_PATTERN.test(trimmed)) {
    return { ...hexToRgb(trimmed), a: 1 };
  }
  const match = RGB_PATTERN.exec(trimmed);
  if (match) {
    const channels = [match[1], match[2], match[3]].map(Number);
    if (channels.some((channel) => channel > MAX_CHANNEL)) {
      return null;
    }
    const [r, g, b] = channels;
    return { r, g, b, a: match[4] === undefined ? 1 : Number(match[4]) };
  }
  const oklchMatch = OKLCH_PATTERN.exec(trimmed);
  if (!oklchMatch) return null;
  const lightness = parseUnitInterval(oklchMatch[1]);
  const chroma = Number.parseFloat(oklchMatch[2]);
  const hue = Number.parseFloat(oklchMatch[3]);
  const alpha = oklchMatch[4] === undefined ? 1 : parseUnitInterval(oklchMatch[4]);
  if (
    lightness === null ||
    alpha === null ||
    !Number.isFinite(chroma) ||
    chroma < 0 ||
    !Number.isFinite(hue)
  ) {
    return null;
  }
  return { ...oklchToRgb(lightness, chroma, hue), a: alpha };
}

/**
 * Alpha 合成:把(可半透明的)前景叠到不透明底色上,`out = fg * a + bg * (1 - a)`。
 * 逐通道四舍五入回 0–255 整数。
 */
export function compositeOver(fg: Rgba, bgColor: string): Rgb {
  const parsedBg = parseColor(bgColor);
  if (parsedBg === null || parsedBg.a < 1) {
    throw new Error(`Invalid opaque background color: ${JSON.stringify(bgColor)}`);
  }
  return compositeOverRgb(fg, parsedBg);
}

/** compositeOver 的 Rgb 版本(底色已是通道值时免去二次解析)。 */
function compositeOverRgb(fg: Rgba, bg: Rgb): Rgb {
  const blend = (fgChannel: number, bgChannel: number): number =>
    Math.round(fgChannel * fg.a + bgChannel * (1 - fg.a));
  return { r: blend(fg.r, bg.r), g: blend(fg.g, bg.g), b: blend(fg.b, bg.b) };
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

/**
 * 两个颜色之间的 WCAG 对比度比(1–21,与参数顺序无关)。
 * 支持 hex 与 rgb()/rgba();前景含 alpha 时先对底色合成再取亮度
 * (底色按不透明处理 —— token 配对的底色一律为不透明表面色)。
 * @throws 任一颜色无法解析时抛错。
 */
export function contrastRatio(fg: string, bg: string): number {
  const fgColor = parseColor(fg);
  const bgColor = parseColor(bg);
  if (!fgColor || !bgColor) {
    throw new Error(`Invalid color: ${JSON.stringify(fgColor === null ? fg : bg)}`);
  }
  const bgRgb: Rgb = { r: bgColor.r, g: bgColor.g, b: bgColor.b };
  const fgRgb =
    fgColor.a >= 1
      ? { r: fgColor.r, g: fgColor.g, b: fgColor.b }
      : compositeOverRgb(fgColor, bgRgb);
  const l1 = relativeLuminance(fgRgb);
  const l2 = relativeLuminance(bgRgb);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * 是否满足 WCAG 2.1 AA 对比度:text 组 ≥ 4.5:1,
 * large-text(大文本)与 graphic(图形元件)组 ≥ 3:1。
 */
export function meetsAA(fg: string, bg: string, kind: ContrastKind = 'text'): boolean {
  const threshold = kind === 'text' ? WCAG_AA_RATIO : WCAG_AA_LARGE_RATIO;
  return contrastRatio(fg, bg) >= threshold;
}
