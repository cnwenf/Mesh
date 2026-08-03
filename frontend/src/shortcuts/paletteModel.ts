/**
 * 面板结果组装模型(纯逻辑层,search-command-palette.md §4.1 / §4.2.1)。
 *
 * - 选项稳定 id:`${type}:${id}`(实体/收藏/对象 recent)/ `cmd:${id}`(命令);
 *   DOM id 为 `palette-opt-{stableId}`,异步补入按稳定 id 维持选择(§4.3.1);
 * - 空 query 唯一数据流(§4.2.1):favorites(服务端,按收藏时间倒序)→
 *   recents(本地,按访问时间倒序,与 favorites 同 target 去重)→
 *   常用命令(本地使用计数倒序,上限 TOP_COMMANDS_LIMIT);
 * - 有 query:Issues / Members & agents / Projects / Views / Chats(服务端分组,
 *   组内保持服务端排序)+ Commands(本地同步过滤)。
 */
import type { IconName } from '../design';
import type { PaletteFavorite, SearchBadge, SearchHighlight, SearchItem } from '../api/search';
import type { RecentEntry } from './recents';
import type { ShortcutCommand } from './registry';

export type PaletteGroupKey =
  'favorites' | 'recents' | 'commands' | 'issues' | 'members' | 'projects' | 'views' | 'chats';

/** 分组组头 i18n 键(§4.1;键值见 .mes127-i18n/palette.json) */
export const GROUP_LABEL_KEYS: Readonly<Record<PaletteGroupKey, string>> = Object.freeze({
  favorites: 'search.group.favorites',
  recents: 'search.group.recents',
  commands: 'search.group.commands',
  issues: 'search.group.issues',
  members: 'search.group.members',
  projects: 'search.group.projects',
  views: 'search.group.views',
  chats: 'search.group.chats',
});

/** 空态常用命令上限 */
export const TOP_COMMANDS_LIMIT = 8;

/** 副标题组装描述:消息目录键 + 参数(§6.18 前端组装,不由服务端拼句) */
export interface PaletteSubtitle {
  readonly key: string;
  readonly params: Readonly<Record<string, string | number>>;
}

export interface PaletteOption {
  /** 稳定 id(`${type}:${id}` / `cmd:${id}` / `fav:{type}:{id}`) */
  readonly stableId: string;
  readonly group: PaletteGroupKey;
  readonly title: string;
  readonly icon: IconName;
  /** 规范深链;命令条目无 url */
  readonly url?: string;
  /** 命令关联快捷键(展示用) */
  readonly combo?: string;
  readonly badge?: SearchBadge;
  readonly highlight?: SearchHighlight;
  readonly subtitle?: PaletteSubtitle;
  /** 原始服务端条目(实体选项) */
  readonly item?: SearchItem;
  /** 原始命令(命令选项) */
  readonly command?: ShortcutCommand;
}

export interface PaletteSection {
  readonly key: PaletteGroupKey;
  readonly labelKey: string;
  readonly options: ReadonlyArray<PaletteOption>;
}

const ICON_BY_KEY: Readonly<Record<string, IconName>> = Object.freeze({
  issue: 'info',
  member: 'user',
  agent: 'agent',
  project: 'folder',
  view: 'board',
  chat_session: 'chat',
});

/** 语义图标键 → 设计系统图标名(未知键落 info,§3.2 icon 为语义键) */
export function iconForSemanticKey(key: string): IconName {
  return ICON_BY_KEY[key] ?? 'info';
}

/** 实体选项稳定 id(`${type}:${id}`) */
export function entityStableId(item: Pick<SearchItem, 'type' | 'id'>): string {
  return `${item.type}:${item.id}`;
}

/** 命令选项稳定 id(`cmd:{id}`) */
export function commandStableId(commandId: string): string {
  return `cmd:${commandId}`;
}

/** 选项 DOM id(palette-opt-{stableId};TopBar 弹层与对话框共用) */
export function optionDomId(stableId: string): string {
  return `palette-opt-${stableId}`;
}

/** 命令本地过滤(label + keywords,同步零延迟;§4.7) */
export function filterCommands(
  commands: ReadonlyArray<ShortcutCommand>,
  query: string,
): ReadonlyArray<ShortcutCommand> {
  const normalized = query.trim().toLowerCase();
  if (normalized === '') {
    return commands;
  }
  return commands.filter(
    (command) =>
      command.label.toLowerCase().includes(normalized) ||
      (command.keywords ?? []).some((keyword) => keyword.toLowerCase().includes(normalized)),
  );
}

/** 实体条目副标题组装描述(按 type 取结构化 context 的消息目录键 + 参数) */
export function subtitleForItem(item: SearchItem): PaletteSubtitle | undefined {
  switch (item.type) {
    case 'issue': {
      const { context } = item;
      return {
        key: 'search.subtitle.issue',
        params: {
          identifier: context.identifier,
          project: context.project?.name ?? '–',
          status: context.status.name,
        },
      };
    }
    case 'member':
    case 'agent': {
      const { context } = item;
      const capacity = context.capacity;
      if (capacity !== undefined) {
        return {
          key: 'search.subtitle.agent',
          params: {
            role: context.role,
            running: capacity.running,
            queued: capacity.queued,
            awaiting: capacity.awaiting_approval,
          },
        };
      }
      return {
        key: 'search.subtitle.member',
        params: { memberType: context.member_type, role: context.role },
      };
    }
    case 'project':
      return {
        key: 'search.subtitle.project',
        params: { key: item.context.key, visibility: item.context.visibility },
      };
    case 'view':
      return { key: 'search.subtitle.view', params: { scope: item.context.scope } };
    case 'chat_session':
      return {
        key: 'search.subtitle.chat',
        params: { count: item.context.participants_count },
      };
    default:
      return undefined;
  }
}

function entityOption(item: SearchItem): PaletteOption {
  return {
    stableId: entityStableId(item),
    group: GROUP_BY_TYPE[item.type],
    title: item.title,
    icon: iconForSemanticKey(item.icon),
    url: item.url,
    badge: item.badge,
    highlight: item.highlight,
    subtitle: subtitleForItem(item),
    item,
  };
}

function commandOption(command: ShortcutCommand): PaletteOption {
  return {
    stableId: commandStableId(command.id),
    group: 'commands',
    title: command.label,
    icon: 'info',
    combo: command.combo,
    command,
  };
}

const GROUP_BY_TYPE: Readonly<Record<SearchItem['type'], PaletteGroupKey>> = Object.freeze({
  issue: 'issues',
  member: 'members',
  agent: 'members',
  project: 'projects',
  view: 'views',
  chat_session: 'chats',
});

/** 查询态分组固定序(§4.1:Issues / Members & agents / Projects / Views / Chats / Commands) */
const QUERY_GROUP_ORDER: ReadonlyArray<PaletteGroupKey> = [
  'issues',
  'members',
  'projects',
  'views',
  'chats',
  'commands',
];

function sectionOf(key: PaletteGroupKey, options: ReadonlyArray<PaletteOption>): PaletteSection {
  return { key, labelKey: GROUP_LABEL_KEYS[key], options };
}

/**
 * 有 query 的分组组装:实体按类型分组(组内保持服务端全序,§4.6),
 * 命令本地同步过滤;空组不出现。
 */
export function buildQuerySections(
  items: ReadonlyArray<SearchItem>,
  commands: ReadonlyArray<ShortcutCommand>,
  query: string,
): ReadonlyArray<PaletteSection> {
  const buckets = new Map<PaletteGroupKey, PaletteOption[]>();
  for (const item of items) {
    const group = GROUP_BY_TYPE[item.type];
    const bucket = buckets.get(group) ?? [];
    bucket.push(entityOption(item));
    buckets.set(group, bucket);
  }
  const commandOptions = filterCommands(commands, query).map(commandOption);
  const sections: PaletteSection[] = [];
  for (const key of QUERY_GROUP_ORDER) {
    const options = key === 'commands' ? commandOptions : (buckets.get(key) ?? []);
    if (options.length > 0) {
      sections.push(sectionOf(key, options));
    }
  }
  return sections;
}

export interface EmptySectionsInput {
  readonly favorites: ReadonlyArray<PaletteFavorite>;
  readonly recents: ReadonlyArray<RecentEntry>;
  readonly commands: ReadonlyArray<ShortcutCommand>;
  /** 命令使用计数(命令 id → 次数) */
  readonly usageCounts: Readonly<Record<string, number>>;
}

function favoriteOption(entry: PaletteFavorite): PaletteOption {
  return {
    stableId: `fav:${entry.target_type}:${entry.target_id}`,
    group: 'favorites',
    title: entry.title ?? entry.target_id,
    icon: iconForSemanticKey(entry.target_type),
    url: entry.url,
  };
}

function sortFavorites(favorites: ReadonlyArray<PaletteFavorite>): ReadonlyArray<PaletteFavorite> {
  // 收藏时间倒序;缺失 created_at 保持服务端序(稳定排序)
  return [...favorites]
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => {
      const atA = a.entry.created_at ?? '';
      const atB = b.entry.created_at ?? '';
      if (atA === atB) return a.index - b.index;
      return atB.localeCompare(atA);
    })
    .map((pair) => pair.entry);
}

function recentObjectTargetKey(entry: RecentEntry): string | null {
  return entry.kind === 'object' ? `${entry.type ?? ''}:${entry.id}` : null;
}

function recentEntryOption(
  entry: RecentEntry,
  commandsById: ReadonlyMap<string, ShortcutCommand>,
): PaletteOption | null {
  if (entry.kind === 'command') {
    const command = commandsById.get(entry.commandId ?? entry.id);
    if (command === undefined) {
      return null; // 命令已不存在(版本更替)→ 不渲染
    }
    return commandOption(command);
  }
  return {
    stableId: `${entry.type ?? 'object'}:${entry.id}`,
    group: 'recents',
    title: entry.title,
    icon: iconForSemanticKey(entry.type ?? ''),
    url: entry.url,
  };
}

function sortCommandsByUsage(
  commands: ReadonlyArray<ShortcutCommand>,
  usageCounts: Readonly<Record<string, number>>,
): ReadonlyArray<ShortcutCommand> {
  return commands
    .map((command, index) => ({ command, index }))
    .sort((a, b) => {
      const countA = usageCounts[a.command.id] ?? 0;
      const countB = usageCounts[b.command.id] ?? 0;
      if (countA !== countB) return countB - countA;
      return a.index - b.index; // 同频按注册序
    })
    .map((pair) => pair.command);
}

/**
 * 空 query 唯一数据流组装(§4.2.1):
 * favorites 区(收藏时间倒序)→ recents 区(访问时间倒序,与 favorites 同 target 去重)→
 * 常用命令区(使用频次倒序,上限 TOP_COMMANDS_LIMIT,与 recents 命令去重)。
 */
export function buildEmptySections(input: EmptySectionsInput): ReadonlyArray<PaletteSection> {
  const { favorites, recents, commands, usageCounts } = input;
  const sections: PaletteSection[] = [];

  const sortedFavorites = sortFavorites(favorites);
  const favoriteTargetKeys = new Set(
    sortedFavorites.map((entry) => `${entry.target_type}:${entry.target_id}`),
  );
  if (sortedFavorites.length > 0) {
    sections.push(sectionOf('favorites', sortedFavorites.map(favoriteOption)));
  }

  const commandsById = new Map(commands.map((command) => [command.id, command]));
  const recentOptions: PaletteOption[] = [];
  const recentCommandIds = new Set<string>();
  const sortedRecents = [...recents].sort((a, b) => b.at - a.at);
  for (const entry of sortedRecents) {
    const targetKey = recentObjectTargetKey(entry);
    if (targetKey !== null && favoriteTargetKeys.has(targetKey)) {
      continue; // 与 favorites 区同 target 去重(§4.2.1)
    }
    const option = recentEntryOption(entry, commandsById);
    if (option === null) {
      continue;
    }
    if (entry.kind === 'command') {
      recentCommandIds.add(entry.commandId ?? entry.id);
    }
    recentOptions.push({ ...option, group: 'recents' });
  }
  if (recentOptions.length > 0) {
    sections.push(sectionOf('recents', recentOptions));
  }

  const topCommands = sortCommandsByUsage(commands, usageCounts)
    .filter((command) => !recentCommandIds.has(command.id))
    .slice(0, TOP_COMMANDS_LIMIT)
    .map(commandOption);
  if (topCommands.length > 0) {
    sections.push(sectionOf('commands', topCommands));
  }

  return sections;
}

/** 分组展平为单一可导航列表(跨组,§4.1 扁平选项序) */
export function flattenSections(
  sections: ReadonlyArray<PaletteSection>,
): ReadonlyArray<PaletteOption> {
  const flat: PaletteOption[] = [];
  for (const section of sections) {
    flat.push(...section.options);
  }
  return flat;
}

/**
 * 方向键移动选择(跨组循环)。当前 id 不在列表(异步补入/首次)时:
 * 向下落首项、向上落末项;在列表时按 delta 循环(§4.3 S7)。
 */
export function moveSelection(
  flat: ReadonlyArray<PaletteOption>,
  currentStableId: string | null,
  delta: 1 | -1,
): string | null {
  if (flat.length === 0) {
    return null;
  }
  const index = flat.findIndex((option) => option.stableId === currentStableId);
  if (index === -1) {
    return delta === 1 ? flat[0].stableId : flat[flat.length - 1].stableId;
  }
  return flat[(index + delta + flat.length) % flat.length].stableId;
}

/**
 * 异步补入后的选择收敛(§4.3.1):选中 id 仍在列表 → 保持;
 * 不在 → 按原索引钳制到新长度(不移位用户即将 Enter 的条目)。
 */
export function reconcileSelection(
  flat: ReadonlyArray<PaletteOption>,
  currentStableId: string | null,
  lastIndex: number,
): { stableId: string | null; index: number } {
  if (flat.length === 0) {
    return { stableId: null, index: -1 };
  }
  if (currentStableId !== null) {
    const found = flat.findIndex((option) => option.stableId === currentStableId);
    if (found !== -1) {
      return { stableId: currentStableId, index: found };
    }
  }
  const clamped = Math.max(0, Math.min(lastIndex, flat.length - 1));
  return { stableId: flat[clamped].stableId, index: clamped };
}

/* ===== 激活(Enter / 点击 / mod+Enter 新标签) ===== */

export interface ActivationDeps {
  navigate: (to: string) => void;
  openExternal: (url: string) => void;
  /** recents 推入与命令计数副作用(注入以便测试与复用) */
  recordRecent?: (option: PaletteOption) => void;
  recordCommandUse?: (commandId: string) => void;
  /** 激活后收尾(关闭面板/弹层) */
  onAfter?: () => void;
}

export interface ActivationOptions {
  /** mod+Enter / mod+click:新标签打开规范深链(§1.3 修饰键新标签) */
  readonly newTab: boolean;
}

/**
 * 对不可信 URL 做运行时类型守卫、同源相对路径校验与 WHATWG 路径归一化。
 * 这里只承担导航安全边界，不冒充业务层的「规范深链」路由校验；具体实体路径
 * 仍由服务端契约与路由表负责。协议 URL、协议相对路径和反斜杠主机逃逸均拒绝。
 */
export function normalizeSameOriginPaletteUrl(value: unknown): string | null {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) {
    return null;
  }
  try {
    const parsed = new URL(value, 'https://mesh.invalid');
    if (parsed.origin !== 'https://mesh.invalid' || parsed.protocol !== 'https:') {
      return null;
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return null;
  }
}

/**
 * 激活一个选项:命令 → run() + 计数 + recent;实体/收藏 → 深链导航或新标签。
 * Enter 竞态安全由调用方在 keydown 瞬间捕获 option 保证(§4.3.1)。
 */
export function activatePaletteOption(
  option: PaletteOption,
  deps: ActivationDeps,
  opts: ActivationOptions,
): void {
  if (option.command !== undefined) {
    option.command.run();
    deps.recordCommandUse?.(option.command.id);
    deps.recordRecent?.(option);
    deps.onAfter?.();
    return;
  }
  const url = normalizeSameOriginPaletteUrl(option.url);
  if (url === null) {
    return;
  }
  if (opts.newTab) {
    deps.openExternal(url);
    deps.onAfter?.();
    return;
  }
  deps.recordRecent?.(option);
  deps.navigate(url);
  deps.onAfter?.();
}
