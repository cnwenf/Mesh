/**
 * 面板/外壳身份上下文 hook:当前用户 id + 活跃工作区(id/slug/角色)。
 *
 * 数据源 `GET /users/me`(member.md §3.1):模块级缓存(按 token 键控)一次,
 * 面板、顶栏弹层与 shell 快捷键注册共用,避免重复请求。recents 三元组隔离的
 * user/workspace 维度亦经此设定(setRecentsScope)。解析失败(离线/401)各字段
 * 为 null,面板降级为仅本地命令(workspaceId null → 不请求,§3.2)。
 *
 * 匿名守卫(必修):无 token 时**绝不**发起 `GET /users/me`。匿名请求的 401
 * 会触发 MES-106 全局兜底(clearToken → 登出清理抹除主题/locale 本地偏好镜像;
 * 非登录页还整页跳 `/login?next=<当前路径>`)——在 OAuth 回调页会打断 code 交换
 * 往返(登录回跳死循环),在登录页会摧毁预置的持久化偏好(暗色首帧被重置为亮)。
 * 故匿名直接降级为仅本地命令;token 出现(SPA 登录)后经 token 依赖补取。
 */
import { useEffect, useState } from 'react';
import { getApiClient } from '../api/instance';
import { useAuthStore } from '../state/authStore';
import { activeWorkspace, fetchMe } from '../features/members/api';
import type { MeResponse, MemberRole } from '../features/members/types';
import { workspaceSlugFromPath } from '../features/search/usePaletteIdentity';

export interface PaletteContextValue {
  readonly userId: string | null;
  readonly workspaceId: string | null;
  readonly workspaceSlug: string | null;
  readonly role: MemberRole | null;
  /** me 解析是否已落地(首帧为 false) */
  readonly resolved: boolean;
}

const EMPTY_CONTEXT: PaletteContextValue = {
  userId: null,
  workspaceId: null,
  workspaceSlug: null,
  role: null,
  resolved: false,
};

/** 匿名降级:各字段 null 且 resolved=true(面板仅本地命令,§3.2)。 */
const ANONYMOUS_CONTEXT: PaletteContextValue = { ...EMPTY_CONTEXT, resolved: true };

/** 模块级缓存:按 token 键控,账号切换即失效;登出/匿名经 reset 清除防串用。 */
let meCache: { token: string; promise: Promise<MeResponse | null> } | null = null;

/** 清除 me 缓存(测试 / 登出 / 账号切换后重新解析) */
export function resetPaletteContextCache(): void {
  meCache = null;
}

function loadMe(token: string): Promise<MeResponse | null> {
  if (meCache === null || meCache.token !== token) {
    meCache = { token, promise: fetchMe(getApiClient()).catch(() => null) };
  }
  return meCache.promise;
}

function contextFromMe(me: MeResponse | null, pathname: string): PaletteContextValue {
  if (me === null) {
    return { ...EMPTY_CONTEXT, resolved: true };
  }
  const routeSlug = workspaceSlugFromPath(pathname);
  const active =
    routeSlug === null
      ? activeWorkspace(me.memberships)
      : (me.memberships.find((membership) => membership.workspace_slug === routeSlug) ?? null);
  return {
    userId: me.user.id,
    workspaceId: active?.workspace_id ?? null,
    workspaceSlug: routeSlug ?? active?.workspace_slug ?? null,
    role: active?.role ?? null,
    resolved: true,
  };
}

interface ContextSnapshot {
  readonly token: string | null;
  readonly pathname: string;
  readonly value: PaletteContextValue;
}

export function usePaletteContext(
  pathname: string = typeof window === 'undefined' ? '/' : window.location.pathname,
): PaletteContextValue {
  const token = useAuthStore((state) => state.token);
  const [snapshot, setSnapshot] = useState<ContextSnapshot>({
    token,
    pathname,
    value: EMPTY_CONTEXT,
  });

  useEffect(() => {
    if (token === null) {
      // 匿名:不请求(见模块头注),并丢弃上一账号缓存防串用。
      resetPaletteContextCache();
      setSnapshot({ token, pathname, value: ANONYMOUS_CONTEXT });
      return;
    }
    let cancelled = false;
    void loadMe(token).then((me) => {
      if (!cancelled) {
        setSnapshot({ token, pathname, value: contextFromMe(me, pathname) });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [token, pathname]);

  // 路由或账号切换后的首帧不得短暂复用上一工作区/上一账号的搜索作用域。
  return snapshot.token === token && snapshot.pathname === pathname
    ? snapshot.value
    : EMPTY_CONTEXT;
}
