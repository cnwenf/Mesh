/**
 * 认证内页面 SEO 契约(search-command-palette.md §3.4 规则 5):
 * 统一 `<meta name="robots" content="noindex">` + `<link rel="canonical">`
 * 指向规范深链。纯 SPA 无 SSR,经 document head 幂等操纵(按属性 upsert);
 * 不为 SEO 牺牲路由一致性(不存在为爬虫保留扁平路由的分支)。
 */
import { useEffect } from 'react';
import { useLocation } from 'react-router';

const ROBOTS_CONTENT = 'noindex';

function upsertMetaRobots(): void {
  let meta = document.querySelector<HTMLMetaElement>('meta[name="robots"]');
  if (meta === null) {
    meta = document.createElement('meta');
    meta.setAttribute('name', 'robots');
    document.head.appendChild(meta);
  }
  meta.setAttribute('content', ROBOTS_CONTENT);
}

function upsertCanonical(href: string): void {
  let link = document.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (link === null) {
    link = document.createElement('link');
    link.setAttribute('rel', 'canonical');
    document.head.appendChild(link);
  }
  link.setAttribute('href', href);
}

export function SeoMeta(): null {
  const location = useLocation();
  useEffect(() => {
    upsertMetaRobots();
    upsertCanonical(window.location.origin + location.pathname + location.search);
  }, [location.pathname, location.search]);
  return null;
}
