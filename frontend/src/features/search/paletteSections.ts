/**
 * 面板行模型与分区组装(search-command-palette.md §4.2.1 空态唯一数据流 / §4.6 排序)。
 *
 * - 空 query 单一组装规则:favorites(收藏时间倒序,服务端唯一来源)→
 *   recents(访问时间倒序,与 favorites 同 target 去重)→ 常用命令(使用频次倒序);
 * - 非空 query:本地命令同步过滤(零延迟先渲染)+ 实体结果按类型分组(组内保持
 *   服务端 §4.6 全序;组间按规范类型序);
 * - 纯函数:不触网、不碰存储,便于单测;行 key 全局稳定(异步补入按 key 保持选中,§4.3.1)。
 */
import type { ShortcutCommand } from '../../shortcuts/registry';
import { recentTargetKey } from './recents';
import type { RecentEntry } from './recents';
import type { FavoriteEntry, SearchResultItem, SearchResultType } from './types';

/** 规范类型分组序(组头渲染与实体分组共用) */
export const TYPE_GROUP_ORDER: readonly SearchResultType[] = [
  'issue',
  'member',
  'agent',
  'project',
  'view',
  'chat_session',
];

/** 统一行模型:命令 / 收藏 / 最近 / 实体四类,key 稳定唯一(aria-activedescendant 用) */
export type PaletteRow =
  | {
      readonly kind: 'command';
      readonly key: string;
      readonly command: ShortcutCommand;
    }
  | {
      readonly kind: 'favorite';
      readonly key: string;
      readonly favorite: FavoriteEntry;
      /** 本地 recents 命中时的可展示标题/深链;缺失时以 target_id 兜底、不可跳转 */
      readonly title: string;
      readonly url: string | null;
      readonly targetType: SearchResultType;
    }
  | {
      readonly kind: 'recent';
      readonly key: string;
      readonly recent: RecentEntry;
    }
  | {
      readonly kind: 'entity';
      readonly key: string;
      readonly item: SearchResultItem;
    };

export const commandRowKey = (commandId: string): string => `cmd:${commandId}`;
export const favoriteRowKey = (favoriteId: string): string => `fav:${favoriteId}`;
export const recentRowKey = (entry: RecentEntry): string => `rec:${recentTargetKey(entry.type, entry.id)}`;
export const entityRowKey = (item: SearchResultItem): string => `ent:${recentTargetKey(item.type, item.id)}`;

export interface EmptyQueryRowsInput {
  readonly favorites: readonly FavoriteEntry[];
  readonly recents: readonly RecentEntry[];
  readonly commands: readonly ShortcutCommand[];
  /** commandId → 使用次数(缺失记 0) */
  readonly counts: Readonly<Record<string, number>>;
}

/** 收藏时间倒序(RFC3339 字符串可直接字典序比较;非法时间沉底) */
function compareFavoriteCreatedAtDesc(a: FavoriteEntry, b: FavoriteEntry): number {
  if (a.created_at === b.created_at) return 0;
  return a.created_at < b.created_at ? 1 : -1;
}

/**
 * 空 query 唯一组装流(§4.2.1):
 * favorites 区(时间倒序)→ recents 区(与 favorites 同 target 去重)→ 常用命令区
 * (频次倒序,同分保持注册序 —— Array.prototype.sort 稳定性保证)。
 */
export function buildEmptyQueryRows(input: EmptyQueryRowsInput): readonly PaletteRow[] {
  const favorites = [...input.favorites].sort(compareFavoriteCreatedAtDesc);
  const favoriteTargets = new Set<string>(
    favorites.map((favorite) => recentTargetKey(favorite.target_type, favorite.target_id)),
  );
  const recentByTarget = new Map<string, RecentEntry>(
    input.recents.map((entry) => [recentTargetKey(entry.type, entry.id), entry]),
  );

  const favoriteRows: PaletteRow[] = favorites.map((favorite) => {
    const resolved = recentByTarget.get(recentTargetKey(favorite.target_type, favorite.target_id));
    return {
      kind: 'favorite',
      key: favoriteRowKey(favorite.id),
      favorite,
      title: resolved?.title ?? favorite.target_id,
      url: resolved?.url ?? null,
      targetType: favorite.target_type,
    };
  });

  const recentRows: PaletteRow[] = input.recents
    .filter((entry) => !favoriteTargets.has(recentTargetKey(entry.type, entry.id)))
    .map((entry): PaletteRow => ({ kind: 'recent', key: recentRowKey(entry), recent: entry }));

  const commandRows: PaletteRow[] = [...input.commands]
    .sort((a, b) => (input.counts[b.id] ?? 0) - (input.counts[a.id] ?? 0))
    .map((command): PaletteRow => ({ kind: 'command', key: commandRowKey(command.id), command }));

  return [...favoriteRows, ...recentRows, ...commandRows];
}

/** 非空 query 本地命令过滤(label + keywords,大小写不敏感,同步零延迟,§4.7) */
export function filterCommands(
  commands: readonly ShortcutCommand[],
  query: string,
): readonly ShortcutCommand[] {
  const normalized = query.trim().toLowerCase();
  if (normalized.length === 0) return commands;
  return commands.filter(
    (command) =>
      command.label.toLowerCase().includes(normalized) ||
      (command.keywords ?? []).some((keyword) => keyword.toLowerCase().includes(normalized)),
  );
}

export interface EntityGroup {
  readonly type: SearchResultType;
  readonly items: readonly SearchResultItem[];
}

/**
 * 实体结果按类型分组:组间按 TYPE_GROUP_ORDER 规范序,组内保持服务端全序(§4.6)。
 * 空组不出现。
 */
export function groupEntityResults(
  items: readonly SearchResultItem[],
): readonly EntityGroup[] {
  const byType = new Map<SearchResultType, SearchResultItem[]>();
  for (const item of items) {
    const bucket = byType.get(item.type) ?? [];
    bucket.push(item);
    byType.set(item.type, bucket);
  }
  const groups: EntityGroup[] = [];
  for (const type of TYPE_GROUP_ORDER) {
    const bucket = byType.get(type);
    if (bucket !== undefined && bucket.length > 0) {
      groups.push({ type, items: bucket });
    }
  }
  return groups;
}

/** 非空 query 行组装:命令区在前(零延迟),实体分组区随后(异步补入) */
export function buildQueryRows(
  commands: readonly ShortcutCommand[],
  entities: readonly SearchResultItem[],
): readonly PaletteRow[] {
  const commandRows: PaletteRow[] = commands.map((command) => ({
    kind: 'command',
    key: commandRowKey(command.id),
    command,
  }));
  const entityRows: PaletteRow[] = entities.map((item) => ({
    kind: 'entity',
    key: entityRowKey(item),
    item,
  }));
  return [...commandRows, ...entityRows];
}
