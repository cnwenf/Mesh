/**
 * 面板本地存储:recents(LRU)+ 命令使用计数 — search-command-palette.md §2.1 / §4.2.1。
 *
 * - recents 纯前端 localStorage,键按 host + user + workspace 三元组隔离(防跨区/跨账号串用);
 *   workspace 维取 id 而非 slug(slug 可被改名,id 稳定)。上限 RECENTS_LIMIT,LRU 淘汰;
 * - 一切读解析做 JSON 守卫:损坏/形状不符 → 空(不抛错、不扩散);
 * - 一切写为不可变更新:返回新数组,不就地修改入参或既有存储结构。
 *
 * host 经 stableHost() 稳定派生:API 基址 origin 优先,同源部署(基址为空)落 location.host。
 * 当前 user/workspace 作用域经 setRecentsScope 由面板数据层在打开时设定。
 */
import { env } from '../env';
import type { SearchItemType } from '../api/search';

/** recents 上限(§2.1,LRU 淘汰) */
export const RECENTS_LIMIT = 20;

const RECENTS_KEY_PREFIX = 'mesh.recents';
const CMD_COUNT_KEY_PREFIX = 'mesh.palette.cmdcount';
const ANONYMOUS_USER = 'anonymous';
const NO_WORKSPACE = 'none';

export type RecentKind = 'object' | 'command';

export interface RecentEntry {
  readonly kind: RecentKind;
  /** 对象类条目的搜索类型(issue/member/agent/project/view/chat_session) */
  readonly type?: SearchItemType;
  readonly id: string;
  readonly title: string;
  readonly url?: string;
  /** 命令条目的命令 id */
  readonly commandId?: string;
  /** 访问时间(epoch ms;LRU 与排序依据) */
  readonly at: number;
}

export interface RecentsScope {
  readonly userId: string;
  readonly workspaceId: string;
}

let currentScope: RecentsScope | null = null;

/** 设定当前 recents 作用域(面板打开时由数据层调用;null 清除) */
export function setRecentsScope(scope: RecentsScope | null): void {
  currentScope = scope;
}

/** 当前 recents 作用域(测试/调试用) */
export function getRecentsScope(): RecentsScope | null {
  return currentScope;
}

/**
 * 稳定 host 维度:API 基址(绝对 URL)取其 origin;同源部署基址为空时取页面 location.host;
 * 均不可用(非浏览器/非法基址)落 'local'。多部署同浏览器经此隔离(§2.1)。
 */
export function stableHost(): string {
  const base = env.apiBaseUrl.trim();
  if (base !== '') {
    try {
      return new URL(base).origin;
    } catch {
      // 非法基址 → 落 location 兜底
    }
  }
  try {
    if (typeof window !== 'undefined' && window.location.host !== '') {
      return window.location.host;
    }
  } catch {
    // 非浏览器环境 → 'local'
  }
  return 'local';
}

/** recents 存储键(三元组隔离,§2.1) */
export function recentsKey(host: string, userId: string, workspaceId: string): string {
  return `${RECENTS_KEY_PREFIX}:${host}:${userId}:${workspaceId}`;
}

/** 命令使用计数存储键(与 recents 同三元组隔离) */
export function commandCountKey(host: string, userId: string, workspaceId: string): string {
  return `${CMD_COUNT_KEY_PREFIX}:${host}:${userId}:${workspaceId}`;
}

function resolveScopeParts(): { host: string; userId: string; workspaceId: string } {
  return {
    host: stableHost(),
    userId: currentScope?.userId ?? ANONYMOUS_USER,
    workspaceId: currentScope?.workspaceId ?? NO_WORKSPACE,
  };
}

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // 存储不可用(隐私模式/配额):recents 为纯体验增强,静默降级
  }
}

const RECENT_KINDS: ReadonlySet<string> = new Set(['object', 'command']);

function isRecentEntry(value: unknown): value is RecentEntry {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.kind === 'string' &&
    RECENT_KINDS.has(candidate.kind) &&
    typeof candidate.id === 'string' &&
    typeof candidate.title === 'string' &&
    typeof candidate.at === 'number'
  );
}

function parseRecents(raw: string | null): RecentEntry[] {
  if (raw === null || raw === '') {
    return [];
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) {
    return [];
  }
  return parsed.filter(isRecentEntry);
}

/** 条目唯一身份:命令按 commandId,对象按 type + id(去重与 LRU 提前依据) */
export function recentIdentity(entry: RecentEntry): string {
  return entry.kind === 'command'
    ? `command:${entry.commandId ?? entry.id}`
    : `object:${entry.type ?? ''}:${entry.id}`;
}

function readRecentsAt(key: string): RecentEntry[] {
  return parseRecents(readStorage(key));
}

/** 读取当前作用域 recents(按 at 倒序;损坏数据已过滤) */
export function listRecents(): RecentEntry[] {
  const { host, userId, workspaceId } = resolveScopeParts();
  const entries = readRecentsAt(recentsKey(host, userId, workspaceId));
  return [...entries].sort((a, b) => b.at - a.at);
}

/**
 * 推入一条 recent:同身份条目去重并置顶(LRU),超上限淘汰最旧。
 * 返回写入后的新数组(不可变;入参不被修改)。
 */
export function pushRecent(entry: RecentEntry): RecentEntry[] {
  const { host, userId, workspaceId } = resolveScopeParts();
  const key = recentsKey(host, userId, workspaceId);
  const identity = recentIdentity(entry);
  const kept = readRecentsAt(key).filter((existing) => recentIdentity(existing) !== identity);
  const next = [entry, ...kept].slice(0, RECENTS_LIMIT);
  writeStorage(key, JSON.stringify(next));
  return [...next];
}

/**
 * 按谓词剔除 recent(如失效对象惰性清理的出口,§4.2.1 局限见 CommandPalette 注释)。
 * 返回剔除后的新数组。
 */
export function removeRecent(predicate: (entry: RecentEntry) => boolean): RecentEntry[] {
  const { host, userId, workspaceId } = resolveScopeParts();
  const key = recentsKey(host, userId, workspaceId);
  const next = readRecentsAt(key).filter((entry) => !predicate(entry));
  writeStorage(key, JSON.stringify(next));
  return [...next];
}

/** 清空当前作用域 recents(切换账号/工作区无需调用——键隔离自然换键) */
export function clearRecents(): void {
  const { host, userId, workspaceId } = resolveScopeParts();
  try {
    window.localStorage.removeItem(recentsKey(host, userId, workspaceId));
  } catch {
    // 存储不可用 → 无操作
  }
}

/* ===== 命令使用计数(空态「常用命令」排序依据,§4.2.1) ===== */

function parseCounts(raw: string | null): Record<string, number> {
  if (raw === null || raw === '') {
    return {};
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return {};
  }
  const result: Record<string, number> = {};
  for (const [commandId, count] of Object.entries(parsed as Record<string, unknown>)) {
    if (typeof count === 'number' && Number.isFinite(count) && count > 0) {
      result[commandId] = count;
    }
  }
  return result;
}

/** 当前作用域命令使用计数(命令 id → 次数;损坏数据过滤为空) */
export function commandUseCounts(): Record<string, number> {
  const { host, userId, workspaceId } = resolveScopeParts();
  return parseCounts(readStorage(commandCountKey(host, userId, workspaceId)));
}

/** 命令执行计数 +1(命令激活时调用;不可变写) */
export function trackCommandUse(commandId: string): void {
  const { host, userId, workspaceId } = resolveScopeParts();
  const key = commandCountKey(host, userId, workspaceId);
  const counts = parseCounts(readStorage(key));
  const next: Record<string, number> = { ...counts, [commandId]: (counts[commandId] ?? 0) + 1 };
  writeStorage(key, JSON.stringify(next));
}
