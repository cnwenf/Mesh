/**
 * Runtime 列表页(runtime.md §4.1):状态点 + 名称 + 类型 + 负载条 + 心跳新鲜度 +
 * 动作(详情 / 暂停 / 恢复 / 删除);状态 / 类型筛选(服务端)+ 名称搜索(客户端)。
 *
 * 顶部队列深度横幅为背压一等信号:订阅 workspace:{ws}:queue,queue.depth_changed
 * 帧即更新(§3.6)。行级实时:workspace:{ws}:runtimes 的 runtime.activated /
 * online / offline / degraded / paused 帧触发整列重拉(README §6.7)。
 * 注册入口:「+ 新增 runtime」打开三段式引导向导(§4.3)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import {
  Banner,
  Button,
  DataView,
  EmptyState,
  ErrorState,
  Select,
  Skeleton,
  StatusDot,
  useToast,
} from '../../design';
import type { StatusDotTone } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { useWorkspaceMembership, workspaceRoute } from '../members/useWorkspaceMembership';
import {
  deleteRuntime,
  listRuntimes,
  pauseRuntime,
  resumeRuntime,
  workspaceQueueChannel,
  workspaceRuntimesChannel,
} from './api';
import { RegisterRuntimeWizard } from './RegisterRuntimeWizard';
import { heartbeatAge } from './format';
import type { RuntimeDetail, RuntimeKind, RuntimeStatus } from './types';
import { RUNTIME_KIND_ORDER, RUNTIME_STATUS_ORDER } from './types';
import './runtimes.css';

const PAGE_LIMIT = 50;
const STATUS_ALL = 'all';
const KIND_ALL = 'all';

/** §6.12:颜色不作唯一信号,状态点必配文本;tone 仅叠加视觉。 */
const STATUS_TONE: Record<RuntimeStatus, StatusDotTone> = {
  pending: 'info',
  online: 'success',
  unavailable: 'danger',
  paused: 'warn',
  draining: 'warn',
  decommissioned: 'neutral',
};

/** 监听即重拉的 runtime 生命周期事件(§3.6)。 */
const RUNTIME_LIST_EVENTS: ReadonlySet<string> = new Set([
  'runtime.activated',
  'runtime.online',
  'runtime.offline',
  'runtime.degraded',
  'runtime.paused',
]);

interface RuntimeRowProps {
  readonly runtime: RuntimeDetail;
  readonly nowMs: number;
  readonly onOpen: (runtime: RuntimeDetail) => void;
  readonly onPause: (runtime: RuntimeDetail) => void;
  readonly onResume: (runtime: RuntimeDetail) => void;
  readonly onDelete: (runtime: RuntimeDetail) => void;
  readonly canManage: boolean;
}

function RuntimeRow(props: RuntimeRowProps): React.JSX.Element {
  const { runtime, nowMs, onOpen, onPause, onResume, onDelete, canManage } = props;
  const t = useT();
  const age = heartbeatAge(runtime.last_heartbeat_at, nowMs);
  const heartbeatLabel =
    age === null
      ? t('runtimes.heartbeat.never')
      : runtime.status === 'online'
        ? t(`runtimes.age.${age.unit}`, { value: age.value })
        : t(`runtimes.offline.${age.unit}`, { value: age.value });
  const loadPct =
    runtime.max_concurrent > 0
      ? Math.min(100, Math.round((runtime.current_load / runtime.max_concurrent) * 100))
      : 0;
  const loadLabel = t('runtimes.loadLabel', {
    load: runtime.current_load,
    max: runtime.max_concurrent,
  });
  const showPause = runtime.status === 'online' || runtime.status === 'draining';
  const showResume = runtime.status === 'paused';
  const showDelete = runtime.status === 'unavailable' || runtime.status === 'decommissioned';

  return (
    <tr className="mesh-runtimes__row" data-testid={`runtime-row-${runtime.id}`}>
      <td className="mesh-runtimes__cell-status" data-label={t('runtimes.col.status')}>
        <StatusDot
          tone={STATUS_TONE[runtime.status]}
          label={t(`runtimes.status.${runtime.status}`)}
        />
      </td>
      <td
        className="mesh-runtimes__cell-name"
        data-label={t('runtimes.col.name')}
        data-testid={`runtime-name-${runtime.id}`}
      >
        {runtime.name}
      </td>
      <td className="mesh-runtimes__cell-kind" data-label={t('runtimes.col.kind')}>
        {t(`runtimes.kind.${runtime.kind}`)}
      </td>
      <td className="mesh-runtimes__cell-load" data-label={t('runtimes.col.load')}>
        <div
          className="mesh-runtimes__load"
          role="meter"
          aria-valuenow={runtime.current_load}
          aria-valuemin={0}
          aria-valuemax={runtime.max_concurrent}
          aria-label={loadLabel}
          data-testid={`runtime-load-${runtime.id}`}
        >
          <div className="mesh-runtimes__load-fill" style={{ width: `${loadPct}%` }} />
        </div>
        <span className="mesh-runtimes__load-text">
          {runtime.current_load}/{runtime.max_concurrent}
        </span>
      </td>
      <td
        className="mesh-runtimes__cell-heartbeat"
        data-label={t('runtimes.col.heartbeat')}
        data-testid={`runtime-heartbeat-${runtime.id}`}
      >
        {heartbeatLabel}
      </td>
      <td className="mesh-runtimes__cell-actions" data-label={t('runtimes.col.actions')}>
        <Button
          variant="ghost"
          size="sm"
          data-testid={`runtime-detail-${runtime.id}`}
          onClick={() => onOpen(runtime)}
        >
          {t('runtimes.action.detail')}
        </Button>
        {canManage && showPause ? (
          <Button
            variant="secondary"
            size="sm"
            data-testid={`runtime-pause-${runtime.id}`}
            onClick={() => onPause(runtime)}
          >
            {t('runtimes.action.pause')}
          </Button>
        ) : null}
        {canManage && showResume ? (
          <Button
            variant="secondary"
            size="sm"
            data-testid={`runtime-resume-${runtime.id}`}
            onClick={() => onResume(runtime)}
          >
            {t('runtimes.action.resume')}
          </Button>
        ) : null}
        {canManage && showDelete ? (
          <Button
            variant="danger"
            size="sm"
            data-testid={`runtime-delete-${runtime.id}`}
            onClick={() => onDelete(runtime)}
          >
            {t('runtimes.action.delete')}
          </Button>
        ) : null}
      </td>
    </tr>
  );
}

export function RuntimesPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const realtime = useRealtimeContext();
  const membershipState = useWorkspaceMembership(client);
  const workspace = membershipState.kind === 'ready' ? membershipState.membership : null;
  const canManage = workspace?.role === 'owner' || workspace?.role === 'admin';

  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get('status') ?? STATUS_ALL;
  const kindFilter = searchParams.get('kind') ?? KIND_ALL;

  const [runtimes, setRuntimes] = useState<RuntimeDetail[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [search, setSearch] = useState('');
  const [queueDepth, setQueueDepth] = useState<number | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const loadRuntimes = useCallback(() => {
    if (workspace === null) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setError(null);
    listRuntimes(client, workspace.workspace_id, {
      status: statusFilter === STATUS_ALL ? undefined : (statusFilter as RuntimeStatus),
      kind: kindFilter === KIND_ALL ? undefined : (kindFilter as RuntimeKind),
      limit: PAGE_LIMIT,
    })
      .then((page) => setRuntimes([...page.data]))
      .catch((err) => setError(err instanceof Error ? err.message : t('state.errorDescription')))
      .finally(() => setIsLoading(false));
  }, [client, workspace, statusFilter, kindFilter, t]);

  useEffect(() => {
    loadRuntimes();
  }, [loadRuntimes, reloadKey]);

  // 心跳新鲜度随时间推进(每秒一拍,仅驱动「Xs 前」文案)。
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // 队列深度背压信号(§3.6 workspace:{ws}:queue / queue.depth_changed)。
  useEffect(() => {
    if (realtime === null || workspace === null) return;
    const channel = workspaceQueueChannel(workspace.workspace_id);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel || frame.event !== 'queue.depth_changed') return;
      const payload = frame.payload as { depth?: number; data?: { depth?: number } };
      const depth = payload.depth ?? payload.data?.depth;
      if (typeof depth === 'number') setQueueDepth(depth);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspace]);

  // 行级实时:生命周期事件 → 整列重拉(与 AgentDetailPage 同模式)。
  useEffect(() => {
    if (realtime === null || workspace === null) return;
    const channel = workspaceRuntimesChannel(workspace.workspace_id);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      if (RUNTIME_LIST_EVENTS.has(frame.event)) setReloadKey((key) => key + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspace]);

  const updateParam = (key: string, value: string | null): void => {
    const params = new URLSearchParams(searchParams);
    if (value === null) params.delete(key);
    else params.set(key, value);
    setSearchParams(params, { replace: true });
  };

  const runAction = async (
    action: () => Promise<unknown>,
    successMessage: string,
  ): Promise<void> => {
    try {
      await action();
      toast.addToast(successMessage, { tone: 'success', closeLabel: t('common.close') });
      setReloadKey((key) => key + 1);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (query === '') return runtimes;
    return runtimes.filter((runtime) => runtime.name.toLowerCase().includes(query));
  }, [runtimes, search]);

  const membershipError = membershipState.kind === 'error' ? t('state.errorDescription') : null;
  const visibleError = membershipError ?? error;
  const loading = membershipState.kind === 'loading' || isLoading;
  const detailPath = (runtimeId: string): string =>
    workspace === null
      ? `/runtimes/${runtimeId}`
      : workspaceRoute(workspace.workspace_slug, `/automations/runtimes/${runtimeId}`);

  return (
    <DataView
      className="mesh-runtimes"
      title={t('runtimes.title')}
      actions={
        workspace !== null && canManage ? (
          <Button
            variant="primary"
            data-testid="new-runtime-button"
            onClick={() => setWizardOpen(true)}
          >
            {t('runtimes.new')}
          </Button>
        ) : undefined
      }
      toolbar={
        <div className="mesh-runtimes__toolbar" role="group" aria-label={t('runtimes.filterLabel')}>
          <Select
            label={t('runtimes.filter.status')}
            value={statusFilter}
            data-testid="runtimes-status-filter"
            onChange={(event) =>
              updateParam('status', event.target.value === STATUS_ALL ? null : event.target.value)
            }
          >
            <option value={STATUS_ALL}>{t('runtimes.filter.all')}</option>
            {RUNTIME_STATUS_ORDER.map((status) => (
              <option key={status} value={status}>
                {t(`runtimes.status.${status}`)}
              </option>
            ))}
          </Select>
          <Select
            label={t('runtimes.filter.kind')}
            value={kindFilter}
            data-testid="runtimes-kind-filter"
            onChange={(event) =>
              updateParam('kind', event.target.value === KIND_ALL ? null : event.target.value)
            }
          >
            <option value={KIND_ALL}>{t('runtimes.filter.all')}</option>
            {RUNTIME_KIND_ORDER.map((kind) => (
              <option key={kind} value={kind}>
                {t(`runtimes.kind.${kind}`)}
              </option>
            ))}
          </Select>
          <label className="mesh-runtimes__search">
            <span className="mesh-runtimes__search-label">{t('common.search')}</span>
            <input
              type="search"
              value={search}
              data-testid="runtimes-search"
              placeholder={t('runtimes.searchPlaceholder')}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
        </div>
      }
    >
      {queueDepth !== null ? (
        <Banner tone={queueDepth > 0 ? 'warn' : 'info'}>
          <span data-testid="runtimes-queue-depth">
            {t('runtimes.queueDepth', { count: queueDepth })}
          </span>
        </Banner>
      ) : null}

      {membershipState.kind === 'no_workspace' && !loading && visibleError === null ? (
        <EmptyState title={t('state.emptyTitle')} description={t('runtimes.noWorkspace')} />
      ) : visibleError !== null ? (
        <ErrorState
          title={t('state.errorTitle')}
          description={visibleError}
          retryLabel={t('common.retry')}
          onRetry={
            membershipState.kind === 'error'
              ? membershipState.retry
              : () => setReloadKey((key) => key + 1)
          }
        />
      ) : loading ? (
        <Skeleton loadingLabel={t('common.loading')} />
      ) : filtered.length === 0 ? (
        <EmptyState title={t('state.emptyTitle')} description={t('runtimes.empty')} />
      ) : (
        <table className="mesh-runtimes__table" data-testid="runtimes-table">
          <caption className="sr-only">{t('runtimes.title')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('runtimes.col.status')}</th>
              <th scope="col">{t('runtimes.col.name')}</th>
              <th scope="col">{t('runtimes.col.kind')}</th>
              <th scope="col">{t('runtimes.col.load')}</th>
              <th scope="col">{t('runtimes.col.heartbeat')}</th>
              <th scope="col">{t('runtimes.col.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((runtime) => (
              <RuntimeRow
                key={runtime.id}
                runtime={runtime}
                nowMs={nowMs}
                canManage={canManage}
                onOpen={(r) => navigate(detailPath(r.id))}
                onPause={(r) =>
                  void runAction(
                    () => pauseRuntime(client, workspace?.workspace_id ?? '', r.id),
                    t('runtimes.toast.paused'),
                  )
                }
                onResume={(r) =>
                  void runAction(
                    () => resumeRuntime(client, workspace?.workspace_id ?? '', r.id),
                    t('runtimes.toast.resumed'),
                  )
                }
                onDelete={(r) =>
                  void runAction(
                    () => deleteRuntime(client, workspace?.workspace_id ?? '', r.id),
                    t('runtimes.toast.deleted'),
                  )
                }
              />
            ))}
          </tbody>
        </table>
      )}

      {workspace !== null ? (
        <RegisterRuntimeWizard
          open={wizardOpen}
          onClose={() => setWizardOpen(false)}
          client={client}
          workspaceId={workspace.workspace_id}
          workspaceSlug={workspace.workspace_slug}
          onRegistered={() => setReloadKey((key) => key + 1)}
        />
      ) : null}
    </DataView>
  );
}
