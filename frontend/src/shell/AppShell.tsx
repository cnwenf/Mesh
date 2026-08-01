/**
 * App shell(README §6.12):TopBar + StatusBanner + Sidebar + <main><Outlet/></main>。
 *
 * - 承载实时连接:useRealtime({url: resolveWsGatewayUrl(env.wsBaseUrl), getToken, enabled, reconciler});
 *   网关地址为绝对 ws(s)://(同源部署 wsBaseUrl 空时由页面 location 派生,MES-106);
 *   reconciler 以 REST 整拉 resync_required 给出的 rest URL 对账(§6.7);
 * - RealtimeContext:向页面(如首页工作区仪表盘)暴露 {state, client};shell 外为 null;
 * - OverlayControls:App 层持有命令面板/帮助层开关,经本 Context 下达 TopBar;
 * - 快捷键/命令注册一次(见 shortcutsRegistration),卸载即注销。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:Context/hook/Provider/外壳组件同文件共存 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { Outlet, useMatch } from 'react-router';
import { MeshApiError, getToken } from '../api';
import { env, resolveWsGatewayUrl } from '../env';
import { useT } from '../i18n';
import { usePreferencesBootstrap } from '../hooks/usePreferencesBootstrap';
import { PollingFallback, useRealtime } from '../realtime';
import type { ConnectionState, RealtimeClient, ResyncRequest } from '../realtime';
import { OnboardingChecklist } from '../features/onboarding';
import { useAuthStore } from '../state/authStore';
import type { RealtimeEventFrame } from '../types/realtime';
import { WorkspaceProvider } from '../workspace/WorkspaceProvider';
import { SeoMeta } from './SeoMeta';
import { ShellShortcutsRegistrar } from './shortcutsRegistration';
import { SIDEBAR_COLLAPSED_STORAGE_KEY } from './navigation';
import { Sidebar } from './Sidebar';
import { MAIN_CONTENT_ID, SkipLink } from './SkipLink';
import { MobileMoreDrawer } from './MobileMoreDrawer';
import { MobileNav } from './MobileNav';
import { StatusBanner } from './StatusBanner';
import { TopBar } from './TopBar';
import './shell.css';

export interface RealtimeContextValue {
  state: ConnectionState;
  client: RealtimeClient;
}

export const RealtimeContext = createContext<RealtimeContextValue | null>(null);

/** shell 外(如登录页/独立渲染的 HomePage)返回 null */
export function useRealtimeContext(): RealtimeContextValue | null {
  return useContext(RealtimeContext);
}

export interface OverlayControls {
  openPalette: () => void;
  openHelp: () => void;
  /** 统一搜索入口:携带查询展开命令面板(design-quality A-02) */
  openSearch: (query: string) => void;
}

const OverlayControlsContext = createContext<OverlayControls | null>(null);

export function useOverlayControls(): OverlayControls | null {
  return useContext(OverlayControlsContext);
}

export interface OverlayControlsProviderProps {
  value: OverlayControls;
  children: ReactNode;
}

export function OverlayControlsProvider(props: OverlayControlsProviderProps): React.JSX.Element {
  return (
    <OverlayControlsContext.Provider value={props.value}>
      {props.children}
    </OverlayControlsContext.Provider>
  );
}

/** resync 对账 REST 允许的路径前缀(后端唯一对账端点 /api/v1/realtime/events,README §6.7) */
const RESYNC_PATH_PREFIX = '/api/v1/';

/**
 * resync 翻页上限:恶意/异常的 next_cursor 永不为空时,无上限会死循环拉取。
 * 超限即停(返回已聚合帧),由后续重连/轮询兜底补齐(MEDIUM-1)。
 */
export const MAX_RESYNC_PAGES = 50;

/**
 * resync rest URL 同源校验(MEDIUM-1):rest 来自 WS 帧(服务端被攻陷或遭 MITM 时不可信),
 * 直接拼接并附 Bearer 会把 token 发往攻击者主机。解析为绝对 URL 后断言与 apiBaseUrl
 * 同源(同源部署 apiBaseUrl 为 '' 时以页面 origin 为基)且路径在 /api/v1/ 之下,
 * 不满足即抛错拒绝 resync(走 reconciler 既有错误路径:客户端退避重试)。
 */
export function resolveResyncUrl(rest: string, base: string = env.apiBaseUrl): string {
  const effectiveBase = base !== '' ? base : window.location.origin;
  let parsed: URL;
  let baseOrigin: string;
  try {
    parsed = new URL(rest, effectiveBase);
    baseOrigin = new URL(effectiveBase).origin;
  } catch {
    throw new MeshApiError({
      status: 0,
      code: 'network',
      message: 'Rejected resync rest URL: unparsable',
    });
  }
  if (parsed.origin !== baseOrigin || !parsed.pathname.startsWith(RESYNC_PATH_PREFIX)) {
    throw new MeshApiError({
      status: 0,
      code: 'network',
      message: 'Rejected resync rest URL: cross-origin or outside ' + RESYNC_PATH_PREFIX,
    });
  }
  return parsed.toString();
}

/** 频道事件 REST 拉取(§6.7 对账端点同形):带 Bearer、按 next_cursor 翻页,聚合为事件帧 */
export async function fetchRestEvents(
  url: string,
  fetchImpl: typeof fetch = fetch,
): Promise<RealtimeEventFrame[]> {
  const token = getToken();
  const frames: RealtimeEventFrame[] = [];
  let cursor: string | null = null;
  let pageUrl = url;
  let pages = 0;
  do {
    pages += 1;
    const response = await fetchImpl(pageUrl, {
      headers: token !== null ? { Authorization: 'Bearer ' + token } : {},
    });
    if (!response.ok) {
      throw new MeshApiError({
        status: response.status,
        code: 'internal_error',
        message: 'HTTP ' + String(response.status),
      });
    }
    const body = (await response.json()) as {
      data: Array<{
        channel: string;
        seq: number;
        event: string;
        payload: Record<string, unknown>;
      }>;
      next_cursor: string | null;
    };
    for (const item of body.data) {
      frames.push({
        op: 'event',
        channel: item.channel,
        seq: item.seq,
        event: item.event,
        payload: item.payload,
      });
    }
    cursor = body.next_cursor;
    if (cursor !== null) {
      pageUrl = url + (url.includes('?') ? '&' : '?') + 'cursor=' + encodeURIComponent(cursor);
    }
  } while (cursor !== null && pages < MAX_RESYNC_PAGES);
  return frames;
}

/**
 * resync REST 对账器工厂:整拉 rest URL 的事件并经 client.ingestReconciledEvent
 * 注入(与实时帧同路径:游标守卫 + 派发);非 2xx 抛错由客户端退避重试。
 * rest 先经 resolveResyncUrl 同源校验(MEDIUM-1):跨源/非 /api/v1/ 前缀的 rest
 * 直接拒绝,绝不发出携带 Bearer 的请求。
 * 导出以供单测直接驱动(真实 resync 经 WS 触发,属 e2e 覆盖)。
 */
export function createReconciler(
  client: RealtimeClient,
  fetchImpl: typeof fetch = fetch,
): (req: ResyncRequest) => Promise<void> {
  return async (req: ResyncRequest): Promise<void> => {
    const frames = await fetchRestEvents(resolveResyncUrl(req.rest), fetchImpl);
    for (const frame of frames) {
      client.ingestReconciledEvent(frame);
    }
  };
}

/** 频道事件拉取 URL(对账/轮询共用) */
export function channelEventsUrl(channel: string, since: number): string {
  return (
    env.apiBaseUrl +
    '/api/v1/realtime/events?channel=' +
    encodeURIComponent(channel) +
    '&since=' +
    String(since)
  );
}

export interface OfflinePollingOptions {
  client: Pick<RealtimeClient, 'getCursor' | 'ingestReconciledEvent'>;
  state: ConnectionState;
  /** 有 token 才轮询(对账端点需 Bearer 鉴权) */
  enabled: boolean;
  /** 需轮询的频道集合(页面已订阅的 workspace:/project:/issue: 频道);为空则不轮询 */
  channels: readonly string[];
  intervalMs?: number;
  fetchImpl?: typeof fetch;
}

/**
 * §3.2 离线降级轮询机制编排:WS 处于 reconnecting/resyncing/offline(非 idle)
 * 时启动 PollingFallback,按频道 seq 水位轮询 REST 对账端点,帧经
 * client.ingestReconciledEvent 与实时帧同路径合并(游标守卫天然去重);
 * 恢复 connected/idle 后自动停止。轮询覆盖调用方已订阅的频道,
 * 使 WS 不可用时(含首订阅竞态重试耗尽后)项目/工作区列表仍能增量更新。
 */
export function useOfflinePolling(opts: OfflinePollingOptions): void {
  const { client, state, enabled, channels } = opts;
  const intervalMs = opts.intervalMs ?? env.pollingIntervalMs;
  const fetchImpl = opts.fetchImpl ?? fetch;
  // 稳定化频道集合,避免每次渲染重建依赖
  const channelsKey = [...channels].sort().join('|');
  useEffect(() => {
    if (!enabled) return;
    if (state === 'connected' || state === 'idle') return;
    if (channelsKey === '') return;
    const fallback = new PollingFallback({
      source: {
        fetch: async (ch: string, since: number) => ({
          frames: await fetchRestEvents(channelEventsUrl(ch, since), fetchImpl),
        }),
      },
      intervalMs,
    });
    const offFrame = fallback.onFrame((frame) => {
      client.ingestReconciledEvent(frame);
    });
    for (const ch of channelsKey.split('|')) {
      const cursor = client.getCursor(ch);
      if (cursor !== undefined) fallback.seedSince(ch, cursor);
      fallback.subscribe(ch);
    }
    fallback.start();
    return () => {
      offFrame();
      fallback.stop();
    };
  }, [state, enabled, client, channelsKey, intervalMs, fetchImpl]);
}

export function AppShell(): React.JSX.Element {
  const t = useT();
  const hasToken = useAuthStore((state) => state.token !== null);
  // theme.md §4.5:登录态偏好回填(GET /me 真源)+ pending 重放触发器。
  usePreferencesBootstrap();

  // reconciler 依赖 client 实例,而 useRealtime 的 options 在首渲染定型:
  // 以稳定包装函数 + ref 延迟委派到绑定真实 client 的实现。
  const reconcilerRef = useRef<((req: ResyncRequest) => Promise<void>) | null>(null);
  const { state, client } = useRealtime({
    // 绝对 ws(s):// 网关地址(MES-106):同源部署 wsBaseUrl 为空时经
    // resolveWsGatewayUrl 由页面 location 派生(WebSocket 构造器拒绝相对地址)。
    url: resolveWsGatewayUrl(env.wsBaseUrl),
    getToken,
    enabled: hasToken,
    reconciler: (req: ResyncRequest) => {
      const impl = reconcilerRef.current;
      return impl ? impl(req) : Promise.resolve();
    },
  });
  reconcilerRef.current = createReconciler(client);

  // 跟踪已订阅频道,供离线轮询覆盖页面订阅的 project:/workspace: 频道。
  const [subscribedChannels, setSubscribedChannels] = useState<readonly string[]>([]);
  useEffect(() => {
    setSubscribedChannels(client.getSubscribedChannels());
    return client.onSubscribeChange(setSubscribedChannels);
  }, [client]);

  // §3.2 离线降级轮询:WS 未连通时自动按频道 seq 水位轮询 REST 事件并经实时
  // 同路径注入;恢复 connected 后自动停止(见 useOfflinePolling)。
  useOfflinePolling({
    client,
    state,
    enabled: hasToken,
    channels: subscribedChannels,
    intervalMs: env.pollingIntervalMs,
  });

  const realtimeValue = useMemo<RealtimeContextValue>(() => ({ state, client }), [state, client]);
  const openPalette = useOverlayOpen('palette');
  const openHelp = useOverlayOpen('help');
  const openSearch = useOverlaySearch();

  // 工作区上下文(workspace.md §4.1):/w/:workspaceSlug/* 命中时以 WorkspaceProvider
  // 包裹整个布局子树(TopBar 切换器 / Sidebar 设置入口 / 页面共享当前工作区)。
  const workspaceMatch = useMatch('/w/:workspaceSlug/*');
  const workspaceSlug = workspaceMatch?.params.workspaceSlug;

  const [mobileMoreOpen, setMobileMoreOpen] = useState(false);
  // 桌面侧栏折叠偏好(design-quality §4.1):持久化到 localStorage,刷新后保持。
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '1';
    } catch {
      return false;
    }
  });
  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((previous) => {
      const next = !previous;
      try {
        window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, next ? '1' : '0');
      } catch {
        // 存储不可用(隐私模式等):仅本次会话内生效,不阻断切换。
      }
      return next;
    });
  }, []);

  const shellClassName = sidebarCollapsed
    ? 'mesh-shell mesh-shell--sidebar-collapsed'
    : 'mesh-shell';

  const layout = (
    <div className={shellClassName}>
      {/* SEO 契约(§3.4):认证内页面 noindex + canonical 规范深链 */}
      <SeoMeta />
      {/* 快捷键/命令注册:位于 WorkspaceProvider 子树内(工作区路由命中时),
          按 slug/role 门控命令集;上下文变化自动重注册 */}
      <ShellShortcutsRegistrar />
      {/* 跳到主内容(design-quality §10.2):键盘首焦直达,绕过顶栏/侧栏 */}
      <SkipLink label={t('a11y.skipLink')} />
      <TopBar
        state={state}
        onOpenPalette={openPalette}
        onOpenHelp={openHelp}
        onOpenSearch={openSearch}
        searchMode="palette"
      />
      <div className="mesh-shell__banner">
        <StatusBanner state={state} />
      </div>
      <Sidebar collapsed={sidebarCollapsed} onToggleCollapsed={toggleSidebar} />
      <main className="mesh-shell__main" id={MAIN_CONTENT_ID} tabIndex={-1}>
        {/* 上手清单(onboarding.md §4.1):核心页面顶部常驻,不适用时自隐藏 */}
        <OnboardingChecklist />
        <Outlet />
      </main>
      {/* 手机导航(design-quality §4.3):0–599px 底部主导航 + 「更多」全高抽屉;
          ≥600px 经 CSS 隐藏,桌面侧栏为唯一主导航。 */}
      <MobileNav onOpenMore={() => setMobileMoreOpen(true)} />
      <MobileMoreDrawer open={mobileMoreOpen} onClose={() => setMobileMoreOpen(false)} />
    </div>
  );

  return (
    <RealtimeContext.Provider value={realtimeValue}>
      {workspaceSlug !== undefined ? (
        <WorkspaceProvider slug={workspaceSlug}>{layout}</WorkspaceProvider>
      ) : (
        layout
      )}
    </RealtimeContext.Provider>
  );
}

/** 从 OverlayControls 取打开器;未提供时为空操作(独立渲染 TopBar 的测试场景) */
function useOverlayOpen(which: 'palette' | 'help'): () => void {
  const controls = useOverlayControls();
  return useCallback(() => {
    if (controls === null) return;
    if (which === 'palette') controls.openPalette();
    else controls.openHelp();
  }, [controls, which]);
}

/** 统一搜索打开器;未提供 OverlayControls 时为空操作(与 useOverlayOpen 同语义) */
function useOverlaySearch(): (query: string) => void {
  const controls = useOverlayControls();
  return useCallback(
    (query: string) => {
      controls?.openSearch(query);
    },
    [controls],
  );
}
