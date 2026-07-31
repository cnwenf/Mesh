/**
 * Issue 列表「保存视图」纯助手(design-quality.md §3.2:保存视图)。
 *
 * 命名的过滤预设经 localStorage 'mesh.issues.savedViews' 持久化,每项为
 * {name, params}(params = URL 过滤键值快照)。纯函数 + 可注入 storage,
 * 存储不可用(隐私模式/SSR)时静默降级为空列表,不抛错打断页面。
 * 边界数据不信任:畸形 JSON / 非数组 / 字段缺失项一律校验后丢弃(§输入校验)。
 */

/** 单个保存视图:名称 + 过滤参数快照(字符串键值,与 URLSearchParams 同构)。 */
export interface SavedView {
  readonly name: string;
  readonly params: Readonly<Record<string, string>>;
}

export const SAVED_VIEWS_STORAGE_KEY = 'mesh.issues.savedViews';

/** 上限 20 项:超过丢弃最旧项(数组尾为最新)。 */
export const MAX_SAVED_VIEWS = 20;

/** 安全读取 window.localStorage;不可用(抛错/无 window)返回 null。 */
export function safeLocalStorage(): Storage | null {
  try {
    if (typeof window === 'undefined') return null;
    return window.localStorage;
  } catch {
    return null;
  }
}

function isStringRecord(value: unknown): value is Record<string, string> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  return Object.values(value as Record<string, unknown>).every((v) => typeof v === 'string');
}

/** 运行时结构守卫:仅接受 {name:非空字符串, params:字符串键值} 的项。 */
export function isSavedView(value: unknown): value is SavedView {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.name === 'string' &&
    candidate.name.trim() !== '' &&
    isStringRecord(candidate.params)
  );
}

/** 解析原始 JSON 文本为合法视图列表;任何畸形输入回退空列表并截断到上限。 */
export function parseSavedViews(raw: string | null): readonly SavedView[] {
  if (raw === null || raw === '') return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.filter(isSavedView).slice(0, MAX_SAVED_VIEWS);
}

/** 从 storage 载入;storage 缺失或读取抛错均回退空列表。 */
export function loadSavedViews(storage: Storage | null = safeLocalStorage()): readonly SavedView[] {
  if (storage === null) return [];
  try {
    return parseSavedViews(storage.getItem(SAVED_VIEWS_STORAGE_KEY));
  } catch {
    return [];
  }
}

/** 持久化视图列表;写入抛错(配额/隐私模式)静默忽略,不影响内存态。 */
export function persistSavedViews(
  views: readonly SavedView[],
  storage: Storage | null = safeLocalStorage(),
): void {
  if (storage === null) return;
  try {
    storage.setItem(SAVED_VIEWS_STORAGE_KEY, JSON.stringify(views.slice(0, MAX_SAVED_VIEWS)));
  } catch {
    // 存储不可写:仅内存态生效,不阻断交互。
  }
}

/**
 * 新增或覆盖同名视图(按 name 去重),并强制上限:超出丢弃最旧项。
 * 返回新数组(不可变)。
 */
export function upsertSavedView(
  views: readonly SavedView[],
  view: SavedView,
): readonly SavedView[] {
  const trimmed: SavedView = { name: view.name.trim(), params: view.params };
  const withoutSameName = views.filter((existing) => existing.name !== trimmed.name);
  const next = [...withoutSameName, trimmed];
  if (next.length <= MAX_SAVED_VIEWS) return next;
  return next.slice(next.length - MAX_SAVED_VIEWS);
}

/** 按名删除;返回新数组(不可变)。 */
export function removeSavedView(views: readonly SavedView[], name: string): readonly SavedView[] {
  return views.filter((view) => view.name !== name);
}
