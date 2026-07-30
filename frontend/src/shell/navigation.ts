/**
 * 全局导航唯一事实源(design-quality §4.1/§4.3):桌面分组侧栏、手机底部主导航、
 * 「更多」抽屉共用同一份「路由 → 入口」定义,杜绝导航与路由不一致、重复中文入口。
 *
 * 分组(§4.1):工作 / 团队 / 运行 / 管理;中文命名写死区分——
 * Autopilots → 自动值守,Runtimes → 运行环境,不再出现两个同名「自动化」。
 * 每个入口携带图标(统一 SVG 图标系统,§7.1),文字标签为语义真源。
 *
 * 依赖方向:shell → design(navigation 只取 IconName 类型与图标语义,不反向)。
 */
import type { IconName } from '../design';

export type NavItemKey =
  | 'home'
  | 'inbox'
  | 'projects'
  | 'issues'
  | 'board'
  | 'cycles'
  | 'members'
  | 'skills'
  | 'squads'
  | 'chat'
  | 'autopilots'
  | 'runtimes'
  | 'insights'
  | 'integrations'
  | 'settings';

export type NavGroupKey = 'work' | 'team' | 'run' | 'admin';

export interface NavItemDef {
  readonly key: NavItemKey;
  /** 静态路由(工作区设置为动态路由,由消费方按角色单独挂载) */
  readonly to: string;
  /** 统一图标(§7.1:导航禁用 emoji/字符图标) */
  readonly icon: IconName;
  /** NavLink 精确匹配(首页 '/' 需要) */
  readonly end?: boolean;
}

export interface NavGroupDef {
  readonly key: NavGroupKey;
  readonly items: ReadonlyArray<NavItemDef>;
}

/** 桌面侧栏四分组(§4.1);「管理」组的工作区设置按角色出现,见消费方 */
export const NAV_GROUPS: ReadonlyArray<NavGroupDef> = [
  {
    key: 'work',
    items: [
      { key: 'home', to: '/', icon: 'home', end: true },
      { key: 'inbox', to: '/inbox', icon: 'inbox' },
      { key: 'projects', to: '/projects', icon: 'folder' },
      { key: 'issues', to: '/issues', icon: 'issues' },
      { key: 'board', to: '/board', icon: 'board' },
      // 周期入口保留于工作组(路由 /cycles 已挂载;Spec §4.1 组表未列,按「可见导航
      // 与路由一致」原则收编,不产生死入口)。
      { key: 'cycles', to: '/cycles', icon: 'cycle' },
    ],
  },
  {
    key: 'team',
    items: [
      { key: 'members', to: '/members', icon: 'user' },
      { key: 'skills', to: '/skills', icon: 'sparkle' },
      { key: 'squads', to: '/squads', icon: 'bot' },
      { key: 'chat', to: '/chat', icon: 'chat' },
    ],
  },
  {
    key: 'run',
    items: [
      // Autopilot 与运行时各占一个明确入口(§4.1 中文:自动值守 / 运行环境)。
      { key: 'autopilots', to: '/autopilots', icon: 'zap' },
      { key: 'runtimes', to: '/runtimes', icon: 'server' },
      { key: 'insights', to: '/insights', icon: 'insights' },
    ],
  },
  {
    key: 'admin',
    items: [
      { key: 'integrations', to: '/integrations', icon: 'plug' },
      { key: 'settings', to: '/settings', icon: 'settings' },
    ],
  },
];

/** 手机底部主导航四主入口(§4.3),「更多」触发键由消费方追加 */
export const MOBILE_PRIMARY_KEYS: ReadonlyArray<NavItemKey> = ['home', 'issues', 'board', 'chat'];

/** 「更多」抽屉承载的入口(§4.3):底部主导航之外的全部入口,按分组顺序展平 */
export const MORE_DRAWER_KEYS: ReadonlyArray<NavItemKey> = [
  'inbox',
  'projects',
  'cycles',
  'members',
  'skills',
  'squads',
  'autopilots',
  'runtimes',
  'insights',
  'integrations',
  'settings',
];

const ITEM_INDEX: ReadonlyMap<NavItemKey, NavItemDef> = new Map(
  NAV_GROUPS.flatMap((group) => group.items.map((item): [NavItemKey, NavItemDef] => [item.key, item])),
);

/** 按入口键取定义(未知键返回 undefined,fail-safe 由调用方兜底) */
export function findNavItem(key: NavItemKey): NavItemDef | undefined {
  return ITEM_INDEX.get(key);
}

/** 侧栏折叠态持久化键(外壳偏好,非业务状态) */
export const SIDEBAR_COLLAPSED_STORAGE_KEY = 'mesh.sidebar.collapsed';
