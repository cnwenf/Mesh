/**
 * useWorkspaceLocale — 获取当前工作区的 settings.default_locale,
 * 供 I18nProvider 的 workspaceDefaultLocale prop 接通协商链第三级(§6.18)。
 *
 * 设计:
 * - 应用启动时异步加载(不阻塞渲染);加载完成前返回 null(协商链跳过本级);
 * - 加载失败静默降级(返回 null,协商链落到系统回退 → en);
 * - 仅在组件挂载时请求一次(工作区默认 locale 为低频变更配置)。
 */
import { useEffect, useState } from 'react';
import type { MeshApiClient } from '../api/client';
import { fetchWorkspaceDefaultLocale } from '../api/workspace';

/**
 * 异步获取工作区默认 locale。
 * @param client - API 客户端实例(null 时不请求,返回 null)
 * @returns 工作区 default_locale 字符串;未加载/加载失败/无工作区时为 null
 */
export function useWorkspaceLocale(client: MeshApiClient | null): string | null {
  const [locale, setLocale] = useState<string | null>(null);

  useEffect(() => {
    // MES-106:client 为 null(未登录,App 层经 hasToken 门控传入)→ 不请求,
    // 并重置为 null(登出后协商链回系统级,不沿用上一账号的工作区默认)。
    if (client === null) {
      setLocale(null);
      return;
    }
    let cancelled = false;

    fetchWorkspaceDefaultLocale(client)
      .then((result) => {
        if (!cancelled) {
          setLocale(result);
        }
      })
      .catch(() => {
        // 静默降级:工作区 API 不可达时协商链跳过本级(§6.18)
        if (!cancelled) {
          setLocale(null);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [client]);

  return locale;
}
