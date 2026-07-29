/**
 * 主题偏好协商链(theme.md §2.2)与首帧 locator 白名单解析(§2.3 ②)。
 *
 * 纯函数真源表——无 DOM/存储副作用,供 ThemeProvider、index.html 内联脚本
 * 镜像逻辑与单元测试共享。镜像 i18n locale 协商链(§6.18),差异仅在链尾:
 * locale 固定回退 en;theme 的 `system` 是动态媒体查询结果。
 */

export type ResolvedTheme = 'light' | 'dark';
export type ThemeSource = 'user' | 'workspace' | 'system';
export type ThemeMode = 'light' | 'dark' | 'system';

export interface ChainInput {
  /** 账号偏好;absent/null = 未表达,跳过第 1 级(继承工作区默认)。 */
  readonly userTheme: ThemeMode | null | undefined;
  /** 工作区默认;absent/null = 默认 `system`(§2.1 T3)。 */
  readonly workspaceDefault: ThemeMode | null | undefined;
  readonly systemPrefersDark: boolean;
}

export interface ChainResult {
  readonly mode: ResolvedTheme;
  readonly source: ThemeSource;
}

/** 值是否为合法主题模式(存储/注入解析的白名单守卫)。 */
export function isThemeMode(value: unknown): value is ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system';
}

/**
 * 解析实际应用的主题(§2.2 真源表):
 * 1. 用户偏好:`light|dark` 终止;显式 `system` **本级终止并跟随 OS**
 *    (不回退工作区默认——「跟随 OS」与「继承工作区」不可合并,§2.1);
 *    absent/null/非法 → 跳过本级;
 * 2. 工作区默认:`light|dark` → 采用;`system`/absent → 落系统;
 * 3. 系统:`prefers-color-scheme`(dark ? dark : light)。
 */
export function resolveThemeChain(input: ChainInput): ChainResult {
  const { userTheme, workspaceDefault, systemPrefersDark } = input;
  const systemMode: ResolvedTheme = systemPrefersDark ? 'dark' : 'light';

  if (userTheme === 'light' || userTheme === 'dark') {
    return { mode: userTheme, source: 'user' };
  }
  if (userTheme === 'system') {
    // 本级终止:忽略工作区默认,跟随操作系统。
    return { mode: systemMode, source: 'user' };
  }
  if (workspaceDefault === 'light' || workspaceDefault === 'dark') {
    return { mode: workspaceDefault, source: 'workspace' };
  }
  return { mode: systemMode, source: 'system' };
}

const SLUG_SEGMENT = /^\/w\/([^/?#]+)/;
const INVITE_ENTRY = /^\/invite(?:[/=?#]|$)/;
/** 无工作区上下文的公开入口(未登录可达)→ `{host}:anon` 分区。 */
const PUBLIC_PATHS = ['/login', '/register', '/auth', '/forgot-password', '/reset-password'];

/**
 * 由当前 URL **同步推导**期望的路由身份分区(§2.3 ②,R3-H3 写死):
 * - `/w/{slug}/…` → `{host}:w:{slug}`
 * - `/invite…` 公开入口 → `{host}:invite`
 * - 其余已登录应用路由 → `{host}:app`
 * - 其余公开页 → `{host}:anon`
 *
 * host 取页面 origin(同源部署下即 API 基址 origin);不依赖任何异步状态。
 */
export function expectedRouteId(href: string): string {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return 'unknown:app';
  }
  const host = url.host;
  const path = url.pathname;
  const slugMatch = SLUG_SEGMENT.exec(path);
  if (slugMatch !== null) {
    return `${host}:w:${slugMatch[1]}`;
  }
  if (INVITE_ENTRY.test(path)) {
    return `${host}:invite`;
  }
  if (PUBLIC_PATHS.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))) {
    return `${host}:anon`;
  }
  return `${host}:app`;
}

/**
 * 解析首帧分区镜像键 `mesh.theme.active`(§2.3 ②)。
 *
 * **id 匹配校验先于 mode 读取**:locator 的 `id` 与当前路由推导的期望
 * route_id 不完全匹配(跨 tab/跨路由残留)→ 丢弃;`mode` 显式白名单,
 * 非 `light|dark` 一律丢弃。任何异常 → null(进 skeleton 兜底)。
 */
export function parseThemeLocator(raw: string | null, expectedId: string): ResolvedTheme | null {
  if (raw === null || raw === '') {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) {
    return null;
  }
  const record = parsed as { id?: unknown; mode?: unknown };
  if (record.id !== expectedId) {
    return null;
  }
  if (record.mode === 'light' || record.mode === 'dark') {
    return record.mode;
  }
  return null;
}
