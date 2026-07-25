/**
 * App shell(README §6.12):TopBar + StatusBanner + Sidebar + <main><Outlet/></main>。
 *
 * - 承载实时连接:useRealtime({url: env.wsBaseUrl + '/ws', getToken, enabled, reconciler});
 *   reconciler 以 REST 整拉 resync_required 给出的 rest URL 对账(§6.7);
 * - RealtimeContext:向页面(如 HomePage 演示区)暴露 {state, client};shell 外为 null;
 * - OverlayControls:App 层持有命令面板/帮助层开关,经本 Context 下达 TopBar;
 * - 快捷键/命令注册一次(见 shortcutsRegistration),卸载即注销。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:Context/hook/Provider/外壳组件同文件共存 */
import { createContext, useCallback, useContext, useEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { MeshApiError, getToken } from '../api';
import { env } from '../env';
import { useT } from '../i18n';
import { useRealtime } from '../realtime';
import type { ConnectionState, RealtimeClient, ResyncRequest } from '../realtime';
import { useAuthStore } from '../state/authStore';
import { registerShellShortcuts } from './shortcutsRegistration';
import { Sidebar } from './Sidebar';
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

/**
 * resync REST 对账:整拉 rest URL;非 2xx 抛 MeshApiError(客户端退避重试)。
 * 导出以供单测直接驱动(真实 resync 经 WS 触发,属 e2e 覆盖)。
 */
export async function reconcile(req: ResyncRequest): Promise<void> {
  const response = await fetch(env.apiBaseUrl + req.rest);
  if (!response.ok) {
    throw new MeshApiError({
      status: response.status,
      code: 'internal_error',
      message: 'HTTP ' + String(response.status),
    });
  }
  await response.text();
}

export function AppShell(): React.JSX.Element {
  const navigate = useNavigate();
  const t = useT();
  const hasToken = useAuthStore((state) => state.token !== null);

  const { state, client } = useRealtime({
    url: env.wsBaseUrl + '/ws',
    getToken,
    enabled: hasToken,
    reconciler: reconcile,
  });

  useEffect(
    () =>
      registerShellShortcuts(navigate, {
        nav: {
          home: t('nav.home'),
          inbox: t('nav.inbox'),
          projects: t('nav.projects'),
          board: t('nav.board'),
          members: t('nav.members'),
          chat: t('nav.chat'),
          automation: t('nav.automation'),
          settings: t('nav.settings'),
        },
        theme: {
          light: t('theme.light'),
          dark: t('theme.dark'),
          system: t('theme.system'),
        },
        actions: {
          themeToggle: t('a11y.themeToggle'),
          newIssue: t('shortcuts.actionNewIssue'),
          focusSearch: t('shortcuts.actionFocusSearch'),
          goInbox: t('shortcuts.actionGoInbox'),
          goBoard: t('shortcuts.actionGoBoard'),
          goMembers: t('shortcuts.actionGoMembers'),
          goAutomation: t('shortcuts.actionGoAutomation'),
        },
      }),
    [navigate, t],
  );

  const realtimeValue = useMemo<RealtimeContextValue>(() => ({ state, client }), [state, client]);
  const openPalette = useOverlayOpen('palette');
  const openHelp = useOverlayOpen('help');

  return (
    <RealtimeContext.Provider value={realtimeValue}>
      <div className="mesh-shell">
        <TopBar state={state} onOpenPalette={openPalette} onOpenHelp={openHelp} />
        <div className="mesh-shell__banner">
          <StatusBanner state={state} />
        </div>
        <Sidebar />
        <main className="mesh-shell__main">
          <Outlet />
        </main>
      </div>
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
