/**
 * 收藏 / recents 目标批量解析(search-command-palette.md §4.2.1 步骤 3 / §5.1)。
 *
 * favorites 端点仅返回 target id(§6.19),无标题/深链;recents 为本地记忆,指向的
 * 对象可能已被删/失权。面板打开时对每个目标做一次**批量存在性 + 标题核验**:
 * - 经各资源既有详情端点并行拉取(复用页面同款 helper),解析出可展示标题 + 规范
 *   深链(§3.4 `/w/{slug}/…`);
 * - 404 / not_found → `missing`(目标已不存在:收藏行不渲染、recent 立即剪枝,§5.1
 *   「打开面板即被清理」);其它错误(网络抖动等)→ `error`(不渲染但**不剪枝**,
 *   避免瞬态故障误删本地数据)。
 *
 * 纯契约层:不碰存储、不触状态,便于单测;调用方(CommandPalette)据结果组装行与剪枝。
 */
import type { MeshApiClient } from '../../api/client';
import { MeshApiError } from '../../api/errors';
import { getAgent } from '../agents/api';
import { getView } from '../board/api';
import { getChatSession } from '../chat/api';
import { getIssue } from '../issues/api';
import { getMember } from '../members/api';
import { getProject } from '../projects/api';
import type { FavoriteEntry, SearchResultType } from './types';

/** 解析所得:可展示标题 + 规范深链(§3.4) */
export interface ResolvedTarget {
  readonly title: string;
  readonly url: string;
}

/** 目标解析三态:ok 可渲染;missing 已不存在(剪枝);error 瞬态故障(保留不剪) */
export type TargetResolution =
  | ({ readonly status: 'ok' } & ResolvedTarget)
  | { readonly status: 'missing' }
  | { readonly status: 'error' };

/** 解析 scope:workspaceId(需 workspace 路径的端点用)+ slug(规范深链组装用) */
export interface ResolveScope {
  readonly workspaceId: string;
  readonly workspaceSlug: string | null;
}

function isMissing(err: unknown): boolean {
  return err instanceof MeshApiError && (err.status === 404 || err.code === 'not_found');
}

/** 规范深链前缀(§3.4);无 slug 上下文时落扁平路由(经迁移解析)。 */
function deepLink(scope: ResolveScope, suffix: string, flat: string): string {
  return scope.workspaceSlug !== null ? `/w/${scope.workspaceSlug}${suffix}` : flat;
}

/**
 * 解析单个目标(type + id)→ 标题 + 规范深链。404/not_found → missing;
 * 其它异常 → error(调用方据此区分「该删」与「暂时不可达」)。
 */
export async function resolveTarget(
  client: MeshApiClient,
  type: SearchResultType,
  id: string,
  scope: ResolveScope,
): Promise<TargetResolution> {
  try {
    switch (type) {
      case 'issue': {
        const detail = await getIssue(client, id);
        return {
          status: 'ok',
          title: detail.title,
          url: deepLink(
            scope,
            `/issues/by-identifier/${encodeURIComponent(detail.identifier)}`,
            `/issues/${detail.id}`,
          ),
        };
      }
      case 'project': {
        const detail = await getProject(client, id);
        return {
          status: 'ok',
          title: detail.name,
          url: deepLink(scope, `/projects/${detail.id}`, `/projects/${detail.id}`),
        };
      }
      case 'view': {
        const detail = await getView(client, id);
        return {
          status: 'ok',
          title: detail.name,
          url: deepLink(scope, `/views/${detail.id}`, `/views/${detail.id}`),
        };
      }
      case 'chat_session': {
        const detail = await getChatSession(client, scope.workspaceId, id);
        return {
          status: 'ok',
          title: detail.title,
          url: deepLink(scope, `/chat/${detail.id}`, `/chat/${detail.id}`),
        };
      }
      case 'member': {
        const detail = await getMember(client, scope.workspaceId, id);
        return {
          status: 'ok',
          title: detail.display_name,
          url: deepLink(scope, `/members/${detail.id}`, `/members/${detail.id}`),
        };
      }
      case 'agent': {
        const detail = await getAgent(client, scope.workspaceId, id);
        return {
          status: 'ok',
          title: detail.display_name,
          url: deepLink(scope, `/agents/${detail.id}`, `/agents/${detail.id}`),
        };
      }
    }
  } catch (err) {
    return isMissing(err) ? { status: 'missing' } : { status: 'error' };
  }
}

/**
 * 批量解析收藏目标(§4.2.1 步骤 3):并行核验,返回 target 键(`${type}:${id}`)→
 * 解析结果映射,仅含 ok 条目(missing/error 不入映射 → 收藏行不渲染,杜绝裸 UUID 死行)。
 */
export async function resolveFavoriteTargets(
  client: MeshApiClient,
  favorites: readonly FavoriteEntry[],
  scope: ResolveScope,
): Promise<ReadonlyMap<string, ResolvedTarget>> {
  const entries = await Promise.all(
    favorites.map(async (favorite) => {
      const resolution = await resolveTarget(
        client,
        favorite.target_type,
        favorite.target_id,
        scope,
      );
      return {
        key: `${favorite.target_type}:${favorite.target_id}`,
        resolution,
      };
    }),
  );
  const map = new Map<string, ResolvedTarget>();
  for (const { key, resolution } of entries) {
    if (resolution.status === 'ok') {
      map.set(key, { title: resolution.title, url: resolution.url });
    }
  }
  return map;
}

/**
 * 批量核验 recents 目标(§5.1 打开即清理):返回**应予保留**的 target 键集合
 * (ok + error 保留;missing 剔除)。调用方经 pruneRecents(validKeys) 持久化剪枝。
 */
export async function collectValidRecentKeys(
  client: MeshApiClient,
  recents: ReadonlyArray<{ readonly type: SearchResultType; readonly id: string }>,
  scope: ResolveScope,
): Promise<ReadonlySet<string>> {
  const entries = await Promise.all(
    recents.map(async (recent) => {
      const resolution = await resolveTarget(client, recent.type, recent.id, scope);
      return { key: `${recent.type}:${recent.id}`, missing: resolution.status === 'missing' };
    }),
  );
  return new Set(entries.filter((entry) => !entry.missing).map((entry) => entry.key));
}
