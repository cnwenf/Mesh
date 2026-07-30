/**
 * 面板身份解析(recents 三元组隔离键与搜索路径 scope 的输入,§2.1 / §3.1)。
 *
 * - workspace 解析序(§3.4 写死):① 当前 URL `/w/{slug}/…` 的 slug(同步可得,后端
 *   接受 UUID 或 slug,§3.1)→ ② 本地 `mesh.last_workspace:{host}:{user}` 记忆(经
 *   成员资格校验)→ ③ 服务端 `users.last_active_workspace_id`(users/me 下发,匹配
 *   成员资格)→ ④ 所属恰一个工作区 → ⑤ 兜底取首个成员身份(纯本地增强,不导航选择页);
 *   无任何成员身份 → 'default';
 * - 同时暴露解析所得成员身份的角色 role(§4.2 no-results「新建 issue」门控输入:
 *   owner/admin/member 可创建,guest 不可);URL slug 命中但成员资格中无该 slug 时
 *   role 为 null(失权/改名,门控自然收紧);
 * - userId 取 GET /users/me 的 user.id;获取失败(mock / 未登录 / 离线)回退 'anon'——
 *   recents 为纯本地增强,身份缺失仅降级隔离粒度,不阻断面板;
 * - me 请求模块级缓存(面板多次打开不重复请求);测试经 resetPaletteIdentityCache 复位。
 */
import { useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api/client';
import { useAuthStore } from '../../state/authStore';
import { readLastWorkspaceSlug } from '../../workspace/lastWorkspace';
import { fetchMe } from '../members/api';
import type { MemberRole, MeResponse, Membership } from '../members/types';

export interface PaletteIdentity {
  readonly userId: string;
  readonly workspaceId: string;
  /** 解析所得工作区 slug(规范深链 /w/{slug}/… 组装用,§3.4);未知为 null */
  readonly workspaceSlug: string | null;
  /** 解析所得成员身份角色;未知/失权为 null(门控据此收紧,§4.2) */
  readonly role: MemberRole | null;
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

export interface ResolvePaletteMembershipOptions {
  /** URL 中的 slug(解析序 ①);null 表示当前不在 /w/{slug}/… 内 */
  readonly slug: string | null;
  readonly userId: string;
  /** GET /users/me 下发的 users.last_active_workspace_id(解析序 ③,可空) */
  readonly lastActiveWorkspaceId?: string | null;
  /** 测试可注入;缺省取 window.localStorage / window.location.host */
  readonly storage?: Storage;
  readonly host?: string;
}

export interface ResolvedPaletteWorkspace {
  readonly workspaceId: string;
  readonly workspaceSlug: string | null;
  readonly role: MemberRole | null;
}

/**
 * 按 §3.4 解析序 ①→⑤ 求面板 scope 的 workspaceId + 成员角色(纯函数,可单测):
 * ① URL slug(后端接受 slug,§3.1;workspaceId 即用 slug,角色取成员资格中同 slug 者);
 * ② 本地记忆 slug 经成员资格校验;③ 服务端 last_active_workspace_id 匹配成员资格;
 * ④ 恰一个成员身份;⑤ 兜底首个成员身份。零成员身份 → default + role null。
 */
export function resolvePaletteMembership(
  memberships: readonly Membership[],
  options: ResolvePaletteMembershipOptions,
): ResolvedPaletteWorkspace {
  const { slug, userId, lastActiveWorkspaceId } = options;

  // ① URL slug 命中:scope 即用 slug,角色取成员资格中同 slug 者(失权 → null)。
  if (slug !== null) {
    const match = memberships.find((membership) => membership.workspace_slug === slug);
    return { workspaceId: slug, workspaceSlug: slug, role: match?.role ?? null };
  }

  const storage =
    options.storage ?? (typeof window === 'undefined' ? undefined : window.localStorage);
  const host = options.host ?? (typeof window === 'undefined' ? 'unknown' : window.location.host);

  // ② 本地记忆 slug(经成员资格校验;改名/退区即失效落后续级)。
  const stored = storage !== undefined ? readLastWorkspaceSlug(userId, storage, host) : null;
  if (stored !== null) {
    const match = memberships.find((membership) => membership.workspace_slug === stored);
    if (match !== undefined) {
      return { workspaceId: match.workspace_id, workspaceSlug: match.workspace_slug, role: match.role };
    }
  }

  // ③ 服务端 last_active_workspace_id 匹配成员资格。
  if (lastActiveWorkspaceId !== undefined && lastActiveWorkspaceId !== null) {
    const match = memberships.find(
      (membership) => membership.workspace_id === lastActiveWorkspaceId,
    );
    if (match !== undefined) {
      return { workspaceId: match.workspace_id, workspaceSlug: match.workspace_slug, role: match.role };
    }
  }

  // ④ 所属恰一个工作区 → 直接采用。
  if (memberships.length === 1) {
    const only = memberships[0];
    if (only !== undefined) {
      return { workspaceId: only.workspace_id, workspaceSlug: only.workspace_slug, role: only.role };
    }
  }

  // ⑤ 兜底:多工作区无线索取首个成员身份(面板为纯增强,不导航选择页)。
  const first = memberships[0];
  if (first !== undefined) {
    return { workspaceId: first.workspace_id, workspaceSlug: first.workspace_slug, role: first.role };
  }

  return { workspaceId: DEFAULT_WORKSPACE_ID, workspaceSlug: null, role: null };
}

export interface UsePaletteIdentityOptions {
  readonly client: MeshApiClient;
  /** 当前路径(默认取 window.location.pathname;路由内组件可传响应式 pathname) */
  readonly pathname?: string;
}

/**
 * 解析 {userId, workspaceId, role}:同步部分(slug)即时可得,me 异步补齐后按
 * §3.4 解析序收窄 workspaceId 并给出成员角色。
 *
 * **未登录不探测 users/me**:面板对公开页(登录页 / OAuth 回调 / 邀请预览)同样
 * 挂载,匿名探测必收 401,会触发 api/unauthorized 全局兜底整页跳 /login——OAuth
 * 回调往返在交换完成前被打断(auth.md §4.5 step 5 全往返破坏)。未登录恒按
 * anon/default 降级呈现(§2.1:身份缺失仅降级隔离粒度,不阻断面板);token 出现
 * (登录/回调换牌)后经 authStore 订阅自动升级为真身解析。
 */
export function usePaletteIdentity(options: UsePaletteIdentityOptions): PaletteIdentity {
  const { client } = options;
  const pathname =
    options.pathname ?? (typeof window === 'undefined' ? '/' : window.location.pathname);
  const slug = workspaceSlugFromPath(pathname);
  const hasToken = useAuthStore((state) => state.token !== null);
  const [identity, setIdentity] = useState<PaletteIdentity>({
    userId: ANONYMOUS_USER_ID,
    workspaceId: slug ?? DEFAULT_WORKSPACE_ID,
    workspaceSlug: slug,
    role: null,
  });

  useEffect(() => {
    if (!hasToken) {
      // 匿名态:不发起 users/me(见函数头注),直接降级呈现。
      setIdentity({
        userId: ANONYMOUS_USER_ID,
        workspaceId: slug ?? DEFAULT_WORKSPACE_ID,
        workspaceSlug: slug,
        role: null,
      });
      return;
    }
    let cancelled = false;
    void loadMe(client).then((me) => {
      if (cancelled) return;
      if (me === null) {
        setIdentity({
          userId: ANONYMOUS_USER_ID,
          workspaceId: slug ?? DEFAULT_WORKSPACE_ID,
          workspaceSlug: slug,
          role: null,
        });
        return;
      }
      const resolved = resolvePaletteMembership(me.memberships, {
        slug,
        userId: me.user.id,
        lastActiveWorkspaceId: me.user.last_active_workspace_id,
      });
      setIdentity({
        userId: me.user.id,
        workspaceId: resolved.workspaceId,
        workspaceSlug: resolved.workspaceSlug,
        role: resolved.role,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [client, slug, hasToken]);

  return identity;
}
