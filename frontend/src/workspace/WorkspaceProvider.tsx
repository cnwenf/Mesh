/**
 * 工作区上下文 Provider(workspace.md §4.1 当前工作区上下文)。
 *
 * 职责:
 * - 经 `GET /workspaces/by-slug/{slug}` 加载当前工作区(全量,含 settings 与 my_role);
 *   历史 slug 解析到当前工作区时**规范化路由**(W6 重定向语义,replace 至现行 slug);
 * - 非成员/不存在/已删除一律 `not_found` 呈现(与后端 404 同一信封,不泄漏存在性,§5.3);
 * - `patch()` 写入设置(PATCH,admin+)并就地更新上下文;`refresh()` 重拉;
 * - realtime(§4.5):订阅 `workspace:{id}` 频道,`workspace.updated` 浅合并 changes,
 *   `workspace.deleted` 提示并返回首页;WS 未连通时按频道水位轮询 REST 对账端点降级。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:Context/hook/Provider 同文件共存 */
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
import { useLocation, useNavigate } from 'react-router';
import { getApiClient } from '../api/instance';
import type { MeshApiClient } from '../api/client';
import { MeshApiError } from '../api/errors';
import { getWorkspaceBySlug, updateWorkspace } from '../api/workspace';
import type { WorkspaceDetail, WorkspacePatch } from '../api/workspace';
import { useT } from '../i18n';
import { useToast } from '../design';
import { PollingFallback } from '../realtime';
import { useRealtimeContext } from '../shell/AppShell';
import { channelEventsUrl, fetchRestEvents } from '../shell/AppShell';
import { env } from '../env';
import { getToken } from '../state/authStore';
import { canDeleteWorkspace, canViewSettings } from './permissions';

export type WorkspaceStatus = 'loading' | 'ready' | 'not_found' | 'error';

export interface WorkspaceContextValue {
  status: WorkspaceStatus;
  workspace: WorkspaceDetail | null;
  error: MeshApiError | null;
  /** 派生门控(呈现级;权威校验在后端) */
  isAdmin: boolean;
  isOwner: boolean;
  /** 重拉工作区(by-slug) */
  refresh(): Promise<void>;
  /** PATCH 更新并就地替换上下文中的工作区对象 */
  patch(changes: WorkspacePatch): Promise<WorkspaceDetail>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

/** shell/工作区路由外返回 null(TopBar 等非工作区上下文消费方) */
export function useOptionalWorkspace(): WorkspaceContextValue | null {
  return useContext(WorkspaceContext);
}

/** 工作区上下文(Provider 外调用抛错 —— 路由组装错误) */
export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext);
  if (value === null) {
    throw new Error('useWorkspace must be used within WorkspaceProvider');
  }
  return value;
}

export interface WorkspaceProviderProps {
  /** 路由参数中的 slug(可能是历史 slug) */
  slug: string;
  /** 测试可注入客户端;缺省全局单例 */
  client?: MeshApiClient;
  children: ReactNode;
}

/** workspace:{id} 频道名(§3.5) */
export function workspaceChannel(workspaceId: string): string {
  return `workspace:${workspaceId}`;
}

export function WorkspaceProvider(props: WorkspaceProviderProps): React.JSX.Element {
  const { slug, children } = props;
  const client = props.client ?? getApiClient();
  const t = useT();
  const navigate = useNavigate();
  const location = useLocation();
  const { addToast } = useToast();

  const [status, setStatus] = useState<WorkspaceStatus>('loading');
  const [workspace, setWorkspace] = useState<WorkspaceDetail | null>(null);
  const [error, setError] = useState<MeshApiError | null>(null);
  // 加载代次守卫:slug 切换时丢弃上一轮的迟到响应。
  const epochRef = useRef(0);

  const load = useCallback(async (): Promise<void> => {
    const epoch = ++epochRef.current;
    setStatus('loading');
    setError(null);
    try {
      const detail = await getWorkspaceBySlug(client, slug);
      if (epochRef.current !== epoch) return;
      setWorkspace(detail);
      setStatus('ready');
    } catch (err) {
      if (epochRef.current !== epoch) return;
      if (err instanceof MeshApiError && (err.status === 404 || err.code === 'not_found')) {
        setWorkspace(null);
        setStatus('not_found');
        return;
      }
      setWorkspace(null);
      setStatus('error');
      setError(err instanceof MeshApiError ? err : new MeshApiError({
        status: 0,
        code: 'unknown',
        message: 'unknown error',
      }));
    }
  }, [client, slug]);

  useEffect(() => {
    void load();
  }, [load]);

  // W6 重定向:历史 slug 解析到当前工作区后规范化 URL(replace,子路径保留)。
  useEffect(() => {
    if (workspace === null) return;
    if (workspace.slug === slug) return;
    const prefix = `/w/${slug}`;
    const rest = location.pathname.startsWith(prefix) ? location.pathname.slice(prefix.length) : '';
    navigate(`/w/${workspace.slug}${rest}`, { replace: true });
  }, [workspace, slug, location.pathname, navigate]);

  const patch = useCallback(
    async (changes: WorkspacePatch): Promise<WorkspaceDetail> => {
      const current = workspace;
      if (current === null) {
        throw new MeshApiError({ status: 0, code: 'not_found', message: 'workspace not loaded' });
      }
      const updated = await updateWorkspace(client, current.id, changes);
      setWorkspace(updated);
      return updated;
    },
    [client, workspace],
  );

  // --- realtime(§4.5)-------------------------------------------------------
  const realtime = useRealtimeContext();
  const workspaceId = workspace !== null ? workspace.id : null;

  const handleDeleted = useCallback(() => {
    addToast(t('workspace.deletedToast'), { tone: 'warn', closeLabel: t('a11y.dismiss') });
    navigate('/');
  }, [addToast, navigate, t]);

  const applyFrame = useCallback(
    (frameEvent: string, payload: Record<string, unknown>): void => {
      if (frameEvent === 'workspace.deleted') {
        handleDeleted();
        return;
      }
      if (frameEvent !== 'workspace.updated') return;
      const changes = payload.changes;
      setWorkspace((prev) => {
        if (prev === null) return prev;
        if (payload.workspace_id !== prev.id) return prev;
        if (typeof changes !== 'object' || changes === null) return prev;
        const nextChanges = changes as Record<string, unknown>;
        const { settings: settingsChange, ...scalarChanges } = nextChanges;
        const nextSettings =
          typeof settingsChange === 'object' && settingsChange !== null
            ? { ...prev.settings, ...(settingsChange as Record<string, unknown>) }
            : prev.settings;
        return { ...prev, ...scalarChanges, settings: nextSettings } as WorkspaceDetail;
      });
    },
    [handleDeleted],
  );

  // WS 订阅(首帧鉴权成功后由客户端携带 resume_from 重订阅)。
  useEffect(() => {
    if (realtime === null || workspaceId === null) return;
    const channel = workspaceChannel(workspaceId);
    realtime.client.subscribe(channel);
    const offFrame = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      applyFrame(frame.event, frame.payload);
    });
    return () => {
      offFrame();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspaceId, applyFrame]);

  // WS 未连通时按频道水位轮询 REST 对账端点降级(§3.5 / §6.7)。
  const realtimeState = realtime !== null ? realtime.state : 'idle';
  useEffect(() => {
    if (realtime === null || workspaceId === null) return;
    if (realtimeState === 'connected' || realtimeState === 'idle') return;
    if (getToken() === null) return;
    const channel = workspaceChannel(workspaceId);
    const fallback = new PollingFallback({
      source: {
        fetch: async (ch: string, since: number) => ({
          frames: await fetchRestEvents(channelEventsUrl(ch, since)),
        }),
      },
      intervalMs: env.pollingIntervalMs,
    });
    const cursor = realtime.client.getCursor(channel);
    if (cursor !== undefined) fallback.seedSince(channel, cursor);
    const offFrame = fallback.onFrame((frame) => {
      realtime.client.ingestReconciledEvent(frame);
    });
    fallback.subscribe(channel);
    fallback.start();
    return () => {
      offFrame();
      fallback.stop();
    };
  }, [realtime, realtimeState, workspaceId]);

  const value = useMemo<WorkspaceContextValue>(() => {
    const role = workspace !== null ? workspace.my_role : 'guest';
    return {
      status,
      workspace,
      error,
      isAdmin: workspace !== null && canViewSettings(role),
      isOwner: workspace !== null && canDeleteWorkspace(role),
      refresh: load,
      patch,
    };
  }, [status, workspace, error, load, patch]);

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

/** 工作区门控呈现:加载/未找到/错误态统一,就绪后渲染 children(§6.12 异常态矩阵)。 */
export function WorkspaceGate(props: { children: ReactNode }): React.JSX.Element | null {
  const { status, error, refresh } = useWorkspace();
  const t = useT();
  if (status === 'loading') {
    return (
      <div className="mesh-ws-gate" role="status" aria-live="polite" data-testid="ws-loading">
        {t('common.loading')}
      </div>
    );
  }
  if (status === 'not_found') {
    return (
      <div className="mesh-ws-gate" data-testid="ws-not-found">
        <h2>{t('workspace.notFoundTitle')}</h2>
        <p>{t('workspace.notFoundDescription')}</p>
      </div>
    );
  }
  if (status === 'error') {
    return (
      <div className="mesh-ws-gate" data-testid="ws-error">
        <h2>{t('state.errorTitle')}</h2>
        <p>{error !== null ? t(`error.${error.code}`) : t('state.errorDescription')}</p>
        <button type="button" onClick={() => void refresh()}>
          {t('common.retry')}
        </button>
      </div>
    );
  }
  return <>{props.children}</>;
}
