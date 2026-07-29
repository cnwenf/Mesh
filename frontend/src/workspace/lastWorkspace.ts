/**
 * active workspace 解析(search-command-palette.md §3.4 解析序,写死)。
 *
 * 旧扁平路由迁移与无工作区上下文入口按序解析目标工作区:
 * ① 当前 URL 已在 /w/{ws}/… 内 → 取 URL 中的 workspace(由调用方先行判断);
 * ② 本地持久化 `mesh.last_workspace:{host}:{user}` 的 slug,经成员资格校验;
 * ③ 服务端 users.last_active_workspace_id(users/me 下发)匹配成员资格;
 * ④ 所属恰一个工作区 → 直接采用;
 * ⑤ 无上下文且多工作区 → null(调用方导航至工作区选择页 /workspace-picker)。
 *
 * 本地键按 host + user 隔离(多部署/多账号同浏览器不串用;workspace 维取
 * slug 供路由直达,id 校验由解析序 ③ 承担)。slug 可被 admin 改名——校验失败
 * 即自然失效,落后续解析级。
 */

const STORAGE_KEY_PREFIX = 'mesh.last_workspace';

export interface WorkspaceMembershipRef {
  readonly workspace_id: string;
  readonly workspace_slug: string;
}

export function lastWorkspaceStorageKey(host: string, userId: string): string {
  return `${STORAGE_KEY_PREFIX}:${host}:${userId}`;
}

export function readLastWorkspaceSlug(
  userId: string,
  storage: Storage = window.localStorage,
  host: string = window.location.host,
): string | null {
  try {
    return storage.getItem(lastWorkspaceStorageKey(host, userId));
  } catch {
    return null;
  }
}

/** 记忆最近活跃工作区(WorkspaceProvider 解析成功 / 选择页选定后调用)。 */
export function recordLastWorkspace(
  userId: string,
  slug: string,
  storage: Storage = window.localStorage,
  host: string = window.location.host,
): void {
  try {
    storage.setItem(lastWorkspaceStorageKey(host, userId), slug);
  } catch {
    // 隐私模式/配额满:记忆为体验增强,失败静默(解析序后续级兜底)。
  }
}

export interface ResolveActiveWorkspaceOptions {
  readonly memberships: readonly WorkspaceMembershipRef[];
  readonly userId: string;
  /** GET /users/me 下发的 users.last_active_workspace_id(可空)。 */
  readonly lastActiveWorkspaceId?: string | null;
  readonly storage?: Storage;
  readonly host?: string;
}

/**
 * 按解析序 ②→④ 求 active workspace slug;无法确定(多工作区且无线索)→ null
 * (解析序 ⑤:工作区选择页)。入参 memberships 已由调用方从 users/me 取得。
 */
export function resolveActiveWorkspaceSlug(options: ResolveActiveWorkspaceOptions): string | null {
  const { memberships, userId, lastActiveWorkspaceId } = options;
  const storage = options.storage ?? window.localStorage;
  const host = options.host ?? window.location.host;

  const activeMemberships = memberships.filter(
    (membership) => membership.workspace_slug !== '' && membership.workspace_id !== '',
  );

  // ② 本地记忆的 slug,经成员资格校验(改名/退区即失效)。
  const stored = readLastWorkspaceSlug(userId, storage, host);
  if (stored !== null) {
    const match = activeMemberships.find((membership) => membership.workspace_slug === stored);
    if (match !== undefined) {
      return match.workspace_slug;
    }
  }

  // ③ 服务端 last_active_workspace_id 匹配成员资格。
  if (lastActiveWorkspaceId !== undefined && lastActiveWorkspaceId !== null) {
    const match = activeMemberships.find(
      (membership) => membership.workspace_id === lastActiveWorkspaceId,
    );
    if (match !== undefined) {
      return match.workspace_slug;
    }
  }

  // ④ 所属恰一个工作区 → 直接采用。
  if (activeMemberships.length === 1) {
    return activeMemberships[0]?.workspace_slug ?? null;
  }

  // ⑤ 无上下文且多工作区(或零工作区)→ 选择页 / 无目标。
  return null;
}
