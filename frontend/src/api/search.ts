/**
 * 统一搜索 API 契约层 — 权威:docs/specs/features/search-command-palette.md §3。
 *
 * - `GET /api/v1/workspaces/{ws}/search?q=&types=&limit=&cursor=` → `{data, next_cursor}`;
 *   服务端只返回对象类结果(六类),命令条目由前端本地合并(§3.1);
 * - 响应条目为稳定 key + 结构化 context/badge/highlight,不返回拼接好的可见句子(§6.18);
 * - highlight offset 单位为原始 title 的 Unicode code point(半开区间 [start,end),§3.2),
 *   前端经 `Array.from(title)` 映射渲染(见 highlightRangesToSpans);
 * - 错误经 MeshApiClient 归一为 MeshApiError:400 validation_error / 403 forbidden /
 *   422 query_cost_exceeded / 429 rate_limited(§3.5)。
 *
 * 同文件承载面板空态的 favorites 数据源(§4.2.1 唯一服务端来源,§6.19 端点)。
 */
import type { MeshApiClient } from './client';

/** 六类搜索对象(search-command-palette.md §3.2 type 枚举,写死) */
export type SearchItemType = 'issue' | 'member' | 'agent' | 'project' | 'view' | 'chat_session';

export interface SearchItemRef {
  readonly id: string;
  readonly name: string;
}

export interface SearchIssueStatus {
  readonly id: string;
  readonly name: string;
  readonly category: string;
}

export interface SearchIssueContext {
  readonly identifier: string;
  readonly project: SearchItemRef | null;
  readonly status: SearchIssueStatus;
}

export interface SearchAgentCapacity {
  readonly running: number;
  readonly queued: number;
  readonly awaiting_approval: number;
}

export interface SearchMemberContext {
  readonly member_type: 'human' | 'agent';
  readonly role: string;
  readonly presence?: string;
  /** agent 容量快照(agent.md「运行中 N / 排队 M / 需审批 K」,§6.12) */
  readonly capacity?: SearchAgentCapacity;
}

export interface SearchProjectContext {
  readonly visibility: string;
  readonly key: string;
}

export interface SearchViewContext {
  readonly scope: 'project' | 'workspace';
  readonly project?: SearchItemRef;
  readonly owner_only?: boolean;
}

export interface SearchChatSessionContext {
  readonly participants_count: number;
  readonly agent?: SearchItemRef;
}

export type SearchItemContext =
  | SearchIssueContext
  | SearchMemberContext
  | SearchProjectContext
  | SearchViewContext
  | SearchChatSessionContext;

/** 徽章:文案经消息目录 key + 参数;color 仅取语义 token 名(§3.2) */
export interface SearchBadge {
  readonly kind: string;
  readonly label_key: string;
  readonly label_params: Readonly<Record<string, string | number>>;
  readonly color: string;
}

/** 命中区间:offset 为原始 title 的 code point 单位,半开区间 [start,end) */
export interface SearchHighlight {
  readonly title: {
    readonly unit: 'codepoint';
    readonly ranges: ReadonlyArray<readonly [number, number]>;
  };
}

interface SearchItemBase {
  readonly id: string;
  /** 主标题原文(未归一化,供渲染与 highlight 映射) */
  readonly title: string;
  /** 类型图标语义键(前端映射图标) */
  readonly icon: string;
  /** 规范深链(§3.4),Enter 直达 */
  readonly url: string;
  readonly badge?: SearchBadge;
  readonly highlight?: SearchHighlight;
}

/** 结果条目统一形状(按 type 判别的联合;context 按类型结构化,§3.2) */
export type SearchItem =
  | (SearchItemBase & { readonly type: 'issue'; readonly context: SearchIssueContext })
  | (SearchItemBase & { readonly type: 'member' | 'agent'; readonly context: SearchMemberContext })
  | (SearchItemBase & { readonly type: 'project'; readonly context: SearchProjectContext })
  | (SearchItemBase & { readonly type: 'view'; readonly context: SearchViewContext })
  | (SearchItemBase & {
      readonly type: 'chat_session';
      readonly context: SearchChatSessionContext;
    });

export interface SearchParams {
  readonly q: string;
  /** 对象类型白名单子集;缺省=全部六类 */
  readonly types?: ReadonlyArray<SearchItemType>;
  /** 默认 20,上限 50(§3.2) */
  readonly limit?: number;
  readonly cursor?: string;
  readonly signal?: AbortSignal;
}

export interface SearchResultPage {
  readonly data: ReadonlyArray<SearchItem>;
  readonly nextCursor: string | null;
}

const searchPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/search`;

/**
 * 全局搜索(对象类结果)。空 q 服务端返回空 data(§3.2)——面板空态由前端本地组装,
 * 调用方应对空 q 跳过请求(useEntitySearch 已内置该短路)。
 */
export async function searchWorkspace(
  client: MeshApiClient,
  workspaceId: string,
  params: SearchParams,
): Promise<SearchResultPage> {
  const envelope = await client.list<SearchItem>(searchPath(workspaceId), {
    query: {
      q: params.q,
      types: params.types !== undefined ? params.types.join(',') : undefined,
      limit: params.limit,
      cursor: params.cursor,
    },
    signal: params.signal,
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 完整 identifier 查询(`KEY-N` 形态,trim 后匹配;§2.2 快路径,跳过防抖) */
export function isIdentifierQuery(q: string): boolean {
  return /^[A-Za-z][A-Za-z0-9]*-\d+$/.test(q.trim());
}

export interface TitleSpan {
  readonly text: string;
  readonly marked: boolean;
}

/**
 * 将 highlight 区间(code point 单位,半开 [start,end))映射为原文渲染分段。
 * 经 `Array.from(title)` 以 code point 遍历(CJK/组合字符偏移正确,§3.2);
 * 越界区间钳制到标题长度;无区间 → 单个未标记分段。纯函数,不返回 HTML。
 */
export function highlightRangesToSpans(
  title: string,
  ranges: ReadonlyArray<readonly [number, number]>,
): ReadonlyArray<TitleSpan> {
  const codepoints = Array.from(title);
  const marked = new Array<boolean>(codepoints.length).fill(false);
  for (const range of ranges) {
    const start = Math.max(0, Math.min(range[0], codepoints.length));
    const end = Math.max(0, Math.min(range[1], codepoints.length));
    for (let i = start; i < end; i += 1) {
      marked[i] = true;
    }
  }
  const spans: TitleSpan[] = [];
  let text = '';
  let current = false;
  for (let i = 0; i < codepoints.length; i += 1) {
    if (i > 0 && marked[i] !== current) {
      spans.push({ text, marked: current });
      text = '';
      current = marked[i];
    }
    current = marked[i];
    text += codepoints[i];
  }
  if (text !== '') {
    spans.push({ text, marked: current });
  }
  return spans;
}

/* ===== 面板空态 favorites 数据源(§4.2.1 唯一服务端来源,§6.19) ===== */

/** 收藏条目;真实端点仅保证 target 元数据,title/url 缺失时由面板详情解析层补齐。 */
export interface PaletteFavorite {
  readonly target_type: string;
  readonly target_id: string;
  readonly title?: string;
  readonly url?: string;
  readonly created_at?: string;
}

/** 可收藏目标类型(§6.19;issue/project/view/chat_session) */
export type FavoriteTargetType = 'issue' | 'project' | 'view' | 'chat_session';

const favoritePath = (targetType: string, targetId: string): string =>
  `/api/v1/favorites/${encodeURIComponent(targetType)}/${encodeURIComponent(targetId)}`;

/** 空 query 面板收藏区数据(§4.2.1):`GET /api/v1/favorites?workspace_id=`,失效目标服务端已不返回 */
export async function listPaletteFavorites(
  client: MeshApiClient,
  workspaceId: string,
): Promise<ReadonlyArray<PaletteFavorite>> {
  const envelope = await client.list<PaletteFavorite>('/api/v1/favorites', {
    query: { workspace_id: workspaceId },
  });
  return envelope.data;
}

/** 收藏(§6.19,PUT 幂等) */
export async function putFavorite(
  client: MeshApiClient,
  targetType: FavoriteTargetType,
  targetId: string,
): Promise<void> {
  await client.request<void>('PUT', favoritePath(targetType, targetId));
}

/** 取消收藏(§6.19,DELETE 幂等) */
export async function deleteFavorite(
  client: MeshApiClient,
  targetType: FavoriteTargetType,
  targetId: string,
): Promise<void> {
  await client.request<void>('DELETE', favoritePath(targetType, targetId));
}

/**
 * 对目标切换收藏态:先列当前收藏判定在否,再 DELETE/PUT(§6.19 两端点均幂等)。
 * 返回切换后的状态(added=已收藏 / removed=已取消)。
 */
export async function toggleFavoriteForTarget(
  client: MeshApiClient,
  workspaceId: string,
  targetType: FavoriteTargetType,
  targetId: string,
): Promise<'added' | 'removed'> {
  const envelope = await client.list<PaletteFavorite>('/api/v1/favorites', {
    query: { workspace_id: workspaceId, target_type: targetType },
  });
  const isFavorite = envelope.data.some((entry) => entry.target_id === targetId);
  if (isFavorite) {
    await deleteFavorite(client, targetType, targetId);
    return 'removed';
  }
  await putFavorite(client, targetType, targetId);
  return 'added';
}
