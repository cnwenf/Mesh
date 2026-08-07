/**
 * favicon 未读徽标(MES-189 L93):纯 SVG data URL,不用 canvas。
 *
 * 浏览器标签页 favicon 叠加未读计数:以 public/favicon.svg 的品牌图形为底
 * (此处内联其形状,该文件为唯一视觉真源),右上角叠加红色计数圆徽
 * (>9 显示 9+);计数归零时恢复原始 href,不留痕。
 *
 * 设计约束:
 * - 不引入 canvas / 图片解码(无解码竞态、无同源污染、SSR 安全);
 * - 原始 href 仅在首次覆盖时暂存,归零恢复后置空(幂等);
 * - count ≤ 0 恒等于「恢复」,负值/NaN 由调用方(useUnreadStore 夹取)兜底。
 */

/** 徽标数字上限:超过显示 9+(favicon 16px 渲染下两位是可读极限)。 */
export const FAVICON_BADGE_MAX = 9;

/** 品牌底色与图形:与 public/favicon.svg 保持一致(改图标时两处同步)。 */
const BRAND_RECT = '<rect width="32" height="32" rx="8" fill="#4f46e5" />';
const BRAND_GLYPH =
  '<path d="M8 22V10l8 7 8-7v12" fill="none" stroke="#fff" stroke-linecap="round" ' +
  'stroke-linejoin="round" stroke-width="3" />' +
  '<circle cx="8" cy="10" r="2" fill="#c7d2fe" />' +
  '<circle cx="16" cy="17" r="2" fill="#c7d2fe" />' +
  '<circle cx="24" cy="10" r="2" fill="#c7d2fe" />';

/** 未读徽标配色:红底白字白描边(深浅底上均与品牌靛底分离清晰)。 */
const BADGE_FILL = '#e5484d';
const BADGE_RING = '#ffffff';

/** 徽标文本:1–9 原样,超过 9 收敛为 9+。 */
export function unreadFaviconLabel(count: number): string {
  return count > FAVICON_BADGE_MAX ? '9+' : String(count);
}

/**
 * 生成带未读徽标的 favicon data URL。
 * @param count - 未读数(调用方保证 > 0;≤0 返回的徽标文本为 "0",调用方不应使用)
 */
export function unreadFaviconDataUrl(count: number): string {
  const label = unreadFaviconLabel(count);
  const isWide = label.length > 1;
  const badgeCx = isWide ? 22.5 : 24;
  const badgeCy = 8;
  const badgeRadius = isWide ? 7.5 : 6;
  const fontSize = isWide ? 8 : 9;
  const badge =
    `<circle cx="${badgeCx}" cy="${badgeCy}" r="${badgeRadius}" fill="${BADGE_FILL}" ` +
    `stroke="${BADGE_RING}" stroke-width="1.5" />` +
    `<text x="${badgeCx}" y="${badgeCy}" fill="#fff" font-size="${fontSize}" font-weight="700" ` +
    `font-family="system-ui, sans-serif" text-anchor="middle" dominant-baseline="central">` +
    `${label}</text>`;
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' +
    `${BRAND_RECT}${BRAND_GLYPH}${badge}</svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

const ICON_SELECTOR = 'link[rel="icon"]';

/** 首次覆盖前的原始 favicon href;归零恢复后置空(模块级,页面唯一)。 */
let originalHref: string | null = null;

/**
 * 按未读数同步 favicon:count > 0 覆盖为徽标版,count ≤ 0 恢复原始图标。
 * 页面无 favicon link 时静默跳过(不抛错——chrome 增强属 best-effort)。
 */
export function applyUnreadFavicon(count: number): void {
  const link = document.querySelector<HTMLLinkElement>(ICON_SELECTOR);
  if (link === null) return;
  if (count <= 0) {
    if (originalHref !== null) {
      link.href = originalHref;
      originalHref = null;
    }
    return;
  }
  if (originalHref === null) originalHref = link.href;
  const next = unreadFaviconDataUrl(count);
  if (link.href !== next) link.href = next;
}
