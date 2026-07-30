/**
 * 面板/外壳身份上下文 hook:当前用户 id + 活跃工作区(id/slug/角色)。
 *
 * 数据源 `GET /users/me`(member.md §3.1):模块级缓存一次,面板、顶栏弹层与
 * shell 快捷键注册共用,避免重复请求。recents 三元组隔离的 user/workspace
 * 维度亦经此设定(setRecentsScope)。解析失败(未登录/离线)各字段为 null,
 * 面板降级为仅本地命令(workspaceId null → 不请求,§3.2)。
 */
import { useEffect, useState } from 'react';
import { getApiClient } from '../api/instance';
import { activeWorkspace, fetchMe } from '../features/members/api';
import type { MeResponse, MemberRole } from '../features/members/types';

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

let meCache: Promise<MeResponse | null> | null = null;

/** 清除 me 缓存(测试 / 登出后重新解析) */
export function resetPaletteContextCache(): void {
  meCache = null;
}

function loadMe(): Promise<MeResponse | null> {
  if (meCache === null) {
    meCache = fetchMe(getApiClient()).catch(() => null);
  }
  return meCache;
}

function contextFromMe(me: MeResponse | null): PaletteContextValue {
  if (me === null) {
    return { ...EMPTY_CONTEXT, resolved: true };
  }
  const active = activeWorkspace(me.memberships);
  return {
    userId: me.user.id,
    workspaceId: active?.workspace_id ?? null,
    workspaceSlug: active?.workspace_slug ?? null,
    role: active?.role ?? null,
    resolved: true,
  };
}

export function usePaletteContext(): PaletteContextValue {
  const [context, setContext] = useState<PaletteContextValue>(EMPTY_CONTEXT);

  useEffect(() => {
    let cancelled = false;
    void loadMe().then((me) => {
      if (!cancelled) {
        setContext(contextFromMe(me));
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return context;
}
