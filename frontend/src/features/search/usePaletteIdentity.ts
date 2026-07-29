/**
 * 面板身份解析(recents 三元组隔离键与搜索路径 scope 的输入,§2.1 / §3.1)。
 *
 * - workspaceId 优先取当前 URL 中的 workspace slug(同步可得;后端接受 UUID 或 slug,
 *   §3.1),其次回退 GET /users/me 的首个成员身份 workspace_id,再无则 'default';
 * - userId 取 GET /users/me 的 user.id;获取失败(mock / 未登录 / 离线)回退 'anon'——
 *   recents 为纯本地增强,身份缺失仅降级隔离粒度,不阻断面板;
 * - me 请求模块级缓存(面板多次打开不重复请求);测试经 resetPaletteIdentityCache 复位。
 */
import { useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api/client';
import { activeWorkspace, fetchMe } from '../members/api';
import type { MeResponse } from '../members/types';

export interface PaletteIdentity {
  readonly userId: string;
  readonly workspaceId: string;
}

export const ANONYMOUS_USER_ID = 'anon';
export const DEFAULT_WORKSPACE_ID = 'default';

/** 从路径解析 workspace slug(`/w/{slug}/…` 规范路由,§3.4);不命中返回 null */
export function workspaceSlugFromPath(pathname: string): string | null {
  const match = /^\/w\/([^/]+)/.exec(pathname);
  return match !== null ? decodeURIComponent(match[1]) : null;
}

let cachedMe: Promise<MeResponse | null> | null = null;

/** me 请求模块级缓存:失败落 null(调用方按回退口径处理),不向 UI 抛错。 */
function loadMe(client: MeshApiClient): Promise<MeResponse | null> {
  if (cachedMe === null) {
    cachedMe = fetchMe(client).catch(() => null);
  }
  return cachedMe;
}

/** 测试复位:清空 me 缓存(真实运行无需调用) */
export function resetPaletteIdentityCache(): void {
  cachedMe = null;
}

export interface UsePaletteIdentityOptions {
  readonly client: MeshApiClient;
  /** 当前路径(默认取 window.location.pathname;路由内组件可传响应式 pathname) */
  readonly pathname?: string;
}

/**
 * 解析 {userId, workspaceId}:同步部分(slug)即时可得,me 异步补齐后收窄。
 * workspace 解析序(§3.4 ①②):URL slug → 最近活跃(首个成员身份)→ default。
 */
export function usePaletteIdentity(options: UsePaletteIdentityOptions): PaletteIdentity {
  const { client } = options;
  const pathname =
    options.pathname ?? (typeof window === 'undefined' ? '/' : window.location.pathname);
  const slug = workspaceSlugFromPath(pathname);
  const [identity, setIdentity] = useState<PaletteIdentity>({
    userId: ANONYMOUS_USER_ID,
    workspaceId: slug ?? DEFAULT_WORKSPACE_ID,
  });

  useEffect(() => {
    let cancelled = false;
    void loadMe(client).then((me) => {
      if (cancelled) return;
      const membership = me === null ? null : activeWorkspace(me.memberships);
      setIdentity({
        userId: me?.user.id ?? ANONYMOUS_USER_ID,
        workspaceId: slug ?? membership?.workspace_id ?? DEFAULT_WORKSPACE_ID,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [client, slug]);

  return identity;
}
