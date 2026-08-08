import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { applyUnreadFavicon, unreadFaviconDataUrl, unreadFaviconLabel } from '../unreadFavicon';

const ORIGINAL_HREF = '/favicon.svg';

function mountFaviconLink(): HTMLLinkElement {
  const link = document.createElement('link');
  link.rel = 'icon';
  link.type = 'image/svg+xml';
  link.href = ORIGINAL_HREF;
  document.head.appendChild(link);
  return link;
}

describe('unreadFavicon(MES-189 L93 未读 favicon 徽标)', () => {
  let link: HTMLLinkElement;

  beforeEach(() => {
    link = mountFaviconLink();
  });

  afterEach(() => {
    applyUnreadFavicon(0); // 复位模块内暂存的原始 href,避免用例串味
    link.remove();
    document.title = '';
  });

  describe('unreadFaviconLabel', () => {
    it('1–9 原样显示', () => {
      expect(unreadFaviconLabel(1)).toBe('1');
      expect(unreadFaviconLabel(9)).toBe('9');
    });

    it('超过 9 收敛为 9+', () => {
      expect(unreadFaviconLabel(10)).toBe('9+');
      expect(unreadFaviconLabel(142)).toBe('9+');
    });
  });

  describe('unreadFaviconDataUrl', () => {
    it('生成 SVG data URL 且包含计数文本', () => {
      const url = unreadFaviconDataUrl(3);
      expect(url.startsWith('data:image/svg+xml,')).toBe(true);
      const svg = decodeURIComponent(url.slice('data:image/svg+xml,'.length));
      expect(svg).toContain('viewBox="0 0 32 32"');
      expect(svg).toContain('>3</text>');
      expect(svg).toContain('#e5484d');
    });

    it('保留品牌底图(靛底 + Mesh 字形)', () => {
      const svg = decodeURIComponent(unreadFaviconDataUrl(1).slice('data:image/svg+xml,'.length));
      expect(svg).toContain('fill="#4f46e5"');
      expect(svg).toContain('M8 22V10l8 7 8-7v12');
    });

    it('两位数徽标加宽半径容纳 9+', () => {
      const wide = decodeURIComponent(unreadFaviconDataUrl(12).slice('data:image/svg+xml,'.length));
      const single = decodeURIComponent(
        unreadFaviconDataUrl(2).slice('data:image/svg+xml,'.length),
      );
      expect(wide).toContain('>9+</text>');
      expect(wide).toContain('r="7.5"');
      expect(single).toContain('r="6"');
    });
  });

  describe('applyUnreadFavicon', () => {
    it('count > 0 覆盖为徽标 data URL', () => {
      applyUnreadFavicon(4);
      expect(link.href.startsWith('data:image/svg+xml,')).toBe(true);
      expect(decodeURIComponent(link.href)).toContain('>4</text>');
    });

    it('count 归零恢复原始 href 且幂等', () => {
      applyUnreadFavicon(4);
      applyUnreadFavicon(0);
      expect(link.href.endsWith(ORIGINAL_HREF)).toBe(true);
      applyUnreadFavicon(0); // 再次归零不抛错、不改变
      expect(link.href.endsWith(ORIGINAL_HREF)).toBe(true);
    });

    it('负值视同归零(恢复原始图标)', () => {
      applyUnreadFavicon(2);
      applyUnreadFavicon(-1);
      expect(link.href.endsWith(ORIGINAL_HREF)).toBe(true);
    });

    it('连续变更不丢失原始 href(恢复的仍是最初图标)', () => {
      applyUnreadFavicon(1);
      applyUnreadFavicon(8);
      applyUnreadFavicon(0);
      expect(link.href.endsWith(ORIGINAL_HREF)).toBe(true);
    });

    it('页面没有 favicon link 时静默跳过', () => {
      link.remove();
      expect(() => applyUnreadFavicon(5)).not.toThrow();
      expect(() => applyUnreadFavicon(0)).not.toThrow();
      link = mountFaviconLink(); // afterEach 需要可移除的 link
    });
  });
});
