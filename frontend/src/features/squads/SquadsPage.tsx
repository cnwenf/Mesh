/**
 * 小队列表页(squad.md §4.1):
 * 搜索(q 300ms 防抖写 URL)+ kind / status 过滤 + 「新建小队」对话框 +
 * 小队卡片(名称 / kind 徽标 / 状态点 / 进行中任务数 / 成员数)。
 * 状态渲染序:无工作区空态 → 错误态(可重试)→ 骨架 → 空态 → 内容。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import {
  Button,
  EmptyState,
  ErrorState,
  Select,
  Skeleton,
  StatusDot,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import { listSquads } from './api';
import { CreateSquadDialog } from './CreateSquadDialog';
import { MemberAvatarWall } from './MemberAvatarWall';
import type { Squad, SquadKind, SquadStatus } from './types';
import { SQUAD_KIND_ORDER, SQUAD_STATUS_ORDER } from './types';
import './squads.css';

const PAGE_LIMIT = 50;
const ALL = 'all';
const SEARCH_DEBOUNCE_MS = 300;

export function SquadsPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);

  const [searchParams, setSearchParams] = useSearchParams();
  const qFilter = searchParams.get('q') ?? '';
  const kindFilter = searchParams.get('kind') ?? ALL;
  const statusFilter = searchParams.get('status') ?? ALL;

  const [qInput, setQInput] = useState(qFilter);
  useEffect(() => setQInput(qFilter), [qFilter]);

  const setParam = useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(searchParams);
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );
  const setParamRef = useRef(setParam);
  setParamRef.current = setParam;

  // 本地搜索 300ms 防抖后写 URL(避免逐键重拉)。
  useEffect(() => {
    if (qInput === qFilter) return;
    const timer = setTimeout(
      () => setParamRef.current('q', qInput === '' ? null : qInput),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [qInput, qFilter]);

  const [workspace, setWorkspace] = useState<Membership | null>(null);
  const [squads, setSquads] = useState<Squad[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);

  const tRef = useRef(t);
  tRef.current = t;
  // 请求序号闸:乱序到达的旧响应不得覆盖新结果(过滤切换竞态防护)。
  const loadSeqRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const me = await fetchMe(client);
      const active = activeWorkspace(me.memberships);
      if (cancelled) return;
      setWorkspace(active);
    })();
    return () => {
      cancelled = true;
    };
  }, [client]);

  const load = useCallback(async (): Promise<void> => {
    if (workspace === null) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setError(null);
    const seq = ++loadSeqRef.current;
    try {
      const page = await listSquads(client, workspace.workspace_id, {
        q: qFilter === '' ? undefined : qFilter,
        kind: kindFilter === ALL ? undefined : (kindFilter as SquadKind),
        status: statusFilter === ALL ? undefined : (statusFilter as SquadStatus),
        limit: PAGE_LIMIT,
      });
      if (seq !== loadSeqRef.current) return;
      setSquads([...page.data]);
    } catch (err: unknown) {
      if (seq !== loadSeqRef.current) return;
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      setError(tRef.current(key));
    } finally {
      if (seq === loadSeqRef.current) {
        setIsLoading(false);
        setHasLoaded(true);
      }
    }
  }, [client, workspace, qFilter, kindFilter, statusFilter]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  if (workspace === null && !isLoading && error === null) {
    return <EmptyState title={t('state.emptyTitle')} description={t('squads.noWorkspace')} />;
  }
  if (error !== null) {
    return (
      <ErrorState
        title={t('state.errorTitle')}
        description={error}
        retryLabel={t('common.retry')}
        onRetry={() => setReloadKey((k) => k + 1)}
      />
    );
  }
  if (isLoading && !hasLoaded) {
    return <Skeleton loadingLabel={t('common.loading')} />;
  }

  return (
    <div className="mesh-squads">
      <header className="mesh-squads__head">
        <h1>{t('squads.pageTitle')}</h1>
        <Button onClick={() => setCreateOpen(true)} data-testid="squad-open-create">
          {t('squads.new')}
        </Button>
      </header>

      <div className="mesh-squads__filters" role="search">
        <input
          type="search"
          value={qInput}
          onChange={(event) => setQInput(event.target.value)}
          placeholder={t('squads.filters.search')}
          aria-label={t('squads.filters.search')}
          data-testid="squad-filter-q"
        />
        <Select
          label={t('squads.filters.kind')}
          value={kindFilter}
          onChange={(event) =>
            setParam('kind', event.target.value === ALL ? null : event.target.value)
          }
        >
          <option value={ALL}>{t('squads.filters.all')}</option>
          {SQUAD_KIND_ORDER.map((value) => (
            <option key={value} value={value}>
              {t(`squads.kind.${value}`)}
            </option>
          ))}
        </Select>
        <Select
          label={t('squads.filters.status')}
          value={statusFilter}
          onChange={(event) =>
            setParam('status', event.target.value === ALL ? null : event.target.value)
          }
        >
          <option value={ALL}>{t('squads.filters.all')}</option>
          {SQUAD_STATUS_ORDER.map((value) => (
            <option key={value} value={value}>
              {t(`squads.status.${value}`)}
            </option>
          ))}
        </Select>
      </div>

      {squads.length === 0 ? (
        <EmptyState
          title={t('squads.empty.title')}
          description={t('squads.empty.description')}
          action={<Button onClick={() => setCreateOpen(true)}>{t('squads.new')}</Button>}
        />
      ) : (
        <ul className="mesh-squads__grid" data-testid="squad-grid">
          {squads.map((squad) => (
            <li key={squad.id} className="mesh-squads__card" data-testid={`squad-card-${squad.id}`}>
              <Link to={`/squads/${squad.id}`} className="mesh-squads__card-link">
                <span className="mesh-squads__card-name">{squad.name}</span>
              </Link>
              <span className="mesh-squads__kind-badge" data-testid={`squad-kind-${squad.id}`}>
                {t(`squads.kind.${squad.kind}`)}
              </span>
              <StatusDot
                tone={squad.status === 'active' ? 'success' : 'neutral'}
                label={t(`squads.status.${squad.status}`)}
              />
              <div className="mesh-squads__card-meta">
                <span data-testid={`squad-tasks-${squad.id}`}>
                  {t('squads.taskCount', { count: squad.active_task_count })}
                </span>
                <span data-testid={`squad-members-${squad.id}`}>
                  {t('squads.memberCount', { count: squad.member_count })}
                </span>
              </div>
              <MemberAvatarWall members={squad.member_preview} />
            </li>
          ))}
        </ul>
      )}

      {createOpen && workspace !== null ? (
        <CreateSquadDialog
          workspace={workspace}
          onCreated={(created) => {
            setSquads((prev) => [created, ...prev.filter((s) => s.id !== created.id)]);
            toast.addToast(t('squads.toast.created'), {
              tone: 'success',
              closeLabel: t('common.close'),
            });
          }}
          onClose={() => setCreateOpen(false)}
        />
      ) : null}
    </div>
  );
}
