/**
 * 全局搜索 / 收藏 API 调用(契约层,search-command-palette.md §3.1 / README §6.14 包络)。
 *
 * - workspace scope 唯一来源 = 路径 {ws}(§3.1):仅拼接调用方解析好的 workspace 标识,
 *   不接受第二来源;
 * - 搜索为列表包络 {data, next_cursor}(整体游标,§3.2);空 q 服务端返回空 data(§3.2);
 * - listAllFavorites 按 next_cursor 有界翻页聚合(§6.19),翻页上限兜底防恶意非空游标。
 */
import type { MeshApiClient } from '../../api/client';
import type { ListEnvelope } from '../../types/envelopes';
import type { FavoriteEntry, SearchResultItem, SearchResultType } from './types';

const FAVORITES_PATH = '/api/v1/favorites';

/** 收藏翻页上限兜底:next_cursor 异常恒非空时防死循环(与 shell MAX_RESYNC_PAGES 同原则) */
const MAX_FAVORITE_PAGES = 10;

export interface SearchWorkspaceParams {
  /** 查询词(≤120 字符;空/缺省 → 服务端返回空集) */
  readonly q: string;
  /** 对象类型白名单子集;缺省 = 全部六类 */
  readonly types?: readonly SearchResultType[];
  /** 页大小(默认 20,上限 50,§3.2) */
  readonly limit?: number;
  /** 整体游标(与 q/types/workspace 绑定,§3.2) */
  readonly cursor?: string;
  /** 过期请求取消(防抖 + 竞态治理,§4.7) */
  readonly signal?: AbortSignal;
}

const searchPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/search`;

/**
 * 全局搜索(对象类结果;命令条目由前端本地合并,§3.1)。
 * 返回列表包络原样(data + next_cursor)。
 */
export async function searchWorkspace(
  client: MeshApiClient,
  workspaceId: string,
  params: SearchWorkspaceParams,
): Promise<ListEnvelope<SearchResultItem>> {
  return client.list<SearchResultItem>(searchPath(workspaceId), {
    query: {
      q: params.q,
      types: params.types !== undefined ? params.types.join(',') : undefined,
      limit: params.limit,
      cursor: params.cursor,
    },
    signal: params.signal,
  });
}

/**
 * 面板空态收藏区数据源(§4.2 空 query 的唯一服务端数据源,§6.19)。
 * 按 created_at 倒序由服务端保证;此处仅做有界翻页聚合。
 */
export async function listAllFavorites(
  client: MeshApiClient,
  workspaceId: string,
): Promise<readonly FavoriteEntry[]> {
  const collected: FavoriteEntry[] = [];
  let cursor: string | null = null;
  let pages = 0;
  do {
    pages += 1;
    const envelope: ListEnvelope<FavoriteEntry> = await client.list<FavoriteEntry>(
      FAVORITES_PATH,
      { query: { workspace_id: workspaceId, cursor: cursor ?? undefined } },
    );
    collected.push(...envelope.data);
    cursor = envelope.next_cursor;
  } while (cursor !== null && pages < MAX_FAVORITE_PAGES);
  return collected;
}
