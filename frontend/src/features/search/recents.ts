/**
 * 最近访问对象(recents)— 纯前端 localStorage(search-command-palette.md §2.1 / §4.2.1)。
 *
 * - 键按 host + user + workspace 三元组隔离(防跨工作区/跨账号串用,§2.1 M3):
 *   `mesh.recents:{host}:{userId}:{workspaceId}`(host = window.location.host);
 * - LRU 上限 20 条:重复访问(同 type + id)提到队首并刷新标题/链接/时间;
 * - 惰性失效清理(§4.2.1):pruneRecents 以调用方批量核验得到的 validIds 剔除失效条目;
 * - 不进服务端(隐私,§5.3);登出不清理(非敏感、按 user 隔离即可);
 * - JSON 解析/存取异常一律降级为空(不静默吞用户数据以外的错误:此处本无用户数据可丢)。
 * 全部不可变:读取返回新数组,写入返回更新后的新数组。
 */
import type { SearchResultType } from './types';

/** LRU 上限(§2.1) */
export const RECENTS_MAX = 20;

const RECENTS_KEY_PREFIX = 'mesh.recents';

export interface RecentEntry {
  readonly type: SearchResultType;
  readonly id: string;
  readonly title: string;
  readonly url: string;
  /** 访问时间(RFC3339 ISO) */
  readonly at: string;
}

/** 可记录为 recent 的最小对象形状(搜索结果条目满足此形) */
export interface RecentRecordable {
  readonly type: SearchResultType;
  readonly id: string;
  readonly title: string;
  readonly url: string;
}

/** 三元组隔离键(§2.1:host 维度覆盖多部署同浏览器场景) */
export function recentsStorageKey(userId: string, workspaceId: string): string {
  const host = typeof window === 'undefined' ? 'unknown' : window.location.host;
  return `${RECENTS_KEY_PREFIX}:${host}:${userId}:${workspaceId}`;
}

const KNOWN_TYPES: ReadonlySet<string> = new Set<string>([
  'issue',
  'member',
  'agent',
  'project',
  'view',
  'chat_session',
]);

function isRecentEntry(value: unknown): value is RecentEntry {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.type === 'string' &&
    KNOWN_TYPES.has(candidate.type) &&
    typeof candidate.id === 'string' &&
    typeof candidate.title === 'string' &&
    typeof candidate.url === 'string' &&
    typeof candidate.at === 'string'
  );
}

/** 读取 recents(队首最近);键缺失 / JSON 损坏 / 非法条目一律安全降级为空或剔除。 */
export function readRecents(userId: string, workspaceId: string): readonly RecentEntry[] {
  const key = recentsStorageKey(userId, workspaceId);
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(key);
  } catch {
    return [];
  }
  if (raw === null || raw === '') return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.filter(isRecentEntry);
}

function writeRecents(userId: string, workspaceId: string, entries: readonly RecentEntry[]): void {
  try {
    window.localStorage.setItem(recentsStorageKey(userId, workspaceId), JSON.stringify(entries));
  } catch {
    // 存储不可用(隐私模式配额等):recents 为纯增强,降级为仅内存态不阻断主流程。
  }
}

/**
 * 记录一次访问:同 target(type + id)去重并提到队首,刷新标题/链接/时间;
 * 超出 RECENTS_MAX 的尾部被淘汰(LRU)。返回更新后的列表(新数组)。
 */
export function recordRecent(
  userId: string,
  workspaceId: string,
  item: RecentRecordable,
  at: string = new Date().toISOString(),
): readonly RecentEntry[] {
  const kept = readRecents(userId, workspaceId).filter(
    (entry) => !(entry.type === item.type && entry.id === item.id),
  );
  const entry: RecentEntry = {
    type: item.type,
    id: item.id,
    title: item.title,
    url: item.url,
    at,
  };
  const updated = [entry, ...kept].slice(0, RECENTS_MAX);
  writeRecents(userId, workspaceId, updated);
  return updated;
}

/**
 * 惰性失效清理(§4.2.1):仅保留 target(type + id)在 validIds 内的条目,
 * 持久化后返回保留列表。validIds 元素形如 `${type}:${id}`(调用方经
 * favorites / 搜索结果批量解析得到;被删/失权对象不残留)。
 */
export function pruneRecents(
  userId: string,
  workspaceId: string,
  validIds: ReadonlySet<string>,
): readonly RecentEntry[] {
  const kept = readRecents(userId, workspaceId).filter((entry) =>
    validIds.has(`${entry.type}:${entry.id}`),
  );
  writeRecents(userId, workspaceId, kept);
  return kept;
}

/** recent 条目的稳定 target 标识(与 pruneRecents 的 validIds 同形) */
export function recentTargetKey(type: SearchResultType, id: string): string {
  return `${type}:${id}`;
}
