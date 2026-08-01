/**
 * 周期页(project.md §1.2.5 / §4.4):周期列表(state 筛选,URL 同源)+ 新建对话框
 * (ends_at >= starts_at 客户端校验)+ 状态前进(planned→active→completed,updateCycle);
 * 完成 auto_roll 周期时响应附带 next_cycle,toast 提示顺延出的新周期名。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, DataView, EmptyState, ErrorState, Select, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import { fetchBurndown } from '../analytics/api';
import type { BurndownData } from '../analytics/types';
import { listCycles, updateCycle } from './api';
import { CreateCycleDialog } from './CreateCycleDialog';
import type { Cycle, CycleState } from './types';
import { CYCLE_STATE_ORDER } from './types';
import { ProgressBar } from './widgets';
import './projects.css';

const PAGE_LIMIT = 20;
const STATE_ALL = 'all';

export function CyclesPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);

  const [searchParams, setSearchParams] = useSearchParams();
  const stateFilter = searchParams.get('state') ?? STATE_ALL;

  const [workspace, setWorkspace] = useState<Membership | null>(null);
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isFetchingMore, setIsFetchingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [burndown, setBurndown] = useState<BurndownData | null>(null);
  const [burndownLoading, setBurndownLoading] = useState(false);
  const [burndownError, setBurndownError] = useState<string | null>(null);
  const [burndownReloadKey, setBurndownReloadKey] = useState(0);

  const activeCycle = cycles.find((cycle) => cycle.state === 'active') ?? null;
  const activeCycleId = activeCycle?.id ?? null;

  useEffect(() => {
    let cancelled = false;
    fetchMe(client)
      .then((me) => {
        if (!cancelled) setWorkspace(activeWorkspace(me.memberships));
      })
      .catch(() => {
        if (!cancelled) setError(t('state.errorDescription'));
      });
    return () => {
      cancelled = true;
    };
  }, [client, t]);

  const loadCycles = useCallback(() => {
    if (workspace === null) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setError(null);
    listCycles(client, workspace.workspace_id, {
      state: stateFilter === STATE_ALL ? undefined : (stateFilter as CycleState),
      limit: PAGE_LIMIT,
    })
      .then((page) => {
        setCycles([...page.data]);
        setNextCursor(page.nextCursor);
      })
      .catch((err) => setError(err instanceof Error ? err.message : t('state.errorDescription')))
      .finally(() => setIsLoading(false));
  }, [client, workspace, stateFilter, t]);

  useEffect(() => {
    loadCycles();
  }, [loadCycles, reloadKey]);

  useEffect(() => {
    if (workspace === null || activeCycleId === null) {
      setBurndown(null);
      setBurndownError(null);
      return;
    }
    let cancelled = false;
    setBurndownLoading(true);
    setBurndownError(null);
    fetchBurndown(client, workspace.workspace_id, {
      cycleId: activeCycleId,
      metric: 'points',
    })
      .then((data) => {
        if (!cancelled) setBurndown(data);
      })
      .catch((err) => {
        if (cancelled) return;
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setBurndownError(t(key));
      })
      .finally(() => {
        if (!cancelled) setBurndownLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeCycleId, burndownReloadKey, client, workspace, t]);

  const handleLoadMore = (): void => {
    if (workspace === null || nextCursor === null || isFetchingMore) return;
    setIsFetchingMore(true);
    listCycles(client, workspace.workspace_id, {
      state: stateFilter === STATE_ALL ? undefined : (stateFilter as CycleState),
      limit: PAGE_LIMIT,
      cursor: nextCursor,
    })
      .then((page) => {
        setCycles((prev) => [...prev, ...page.data]);
        setNextCursor(page.nextCursor);
      })
      .catch(() => {
        toast.addToast(t('common.unknownError'), { tone: 'danger', closeLabel: t('common.close') });
      })
      .finally(() => setIsFetchingMore(false));
  };

  const advance = async (cycle: Cycle, nextState: CycleState): Promise<void> => {
    try {
      const result = await updateCycle(client, cycle.id, { state: nextState });
      setCycles((prev) =>
        prev.map((existing) =>
          existing.id === cycle.id ? { ...existing, ...result, id: existing.id } : existing,
        ),
      );
      if (result.next_cycle !== undefined) {
        toast.addToast(t('cycles.rolledToast', { name: result.next_cycle.name }), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
        setCycles((prev) => [result.next_cycle as Cycle, ...prev]);
      } else {
        toast.addToast(t('cycles.updated'), { tone: 'success', closeLabel: t('common.close') });
      }
    } catch (err) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    }
  };

  const remainingPoints = burndown?.actual.at(-1)?.remaining ?? burndown?.total ?? 0;
  const burndownProgress =
    burndown !== null && burndown.total > 0
      ? (burndown.total - remainingPoints) / burndown.total
      : 0;

  return (
    <main className="mesh-projects">
      <DataView
        title={t('cycles.title')}
        actions={
          workspace !== null ? (
            <Button
              variant="primary"
              data-testid="new-cycle-button"
              onClick={() => setCreateOpen(true)}
            >
              {t('cycles.new')}
            </Button>
          ) : null
        }
        toolbar={
          <div className="mesh-projects__toolbar" role="group" aria-label={t('cycles.filterLabel')}>
            <Select
              label={t('cycles.filter.state')}
              value={stateFilter}
              data-testid="cycles-state-filter"
              onChange={(event) => {
                const params = new URLSearchParams(searchParams);
                if (event.target.value === STATE_ALL) params.delete('state');
                else params.set('state', event.target.value);
                setSearchParams(params, { replace: true });
              }}
            >
              <option value={STATE_ALL}>{t('cycles.state.all')}</option>
              {CYCLE_STATE_ORDER.map((state) => (
                <option key={state} value={state}>
                  {t(`cycles.state.${state}`)}
                </option>
              ))}
            </Select>
          </div>
        }
        footer={
          nextCursor !== null ? (
            <Button
              variant="secondary"
              data-testid="cycles-load-more"
              disabled={isFetchingMore}
              onClick={handleLoadMore}
            >
              {t('projects.loadMore')}
            </Button>
          ) : undefined
        }
      >
        {activeCycle !== null ? (
          <section
            className="mesh-projects__cycle-summary"
            data-testid="cycle-burndown-summary"
            aria-labelledby="cycle-burndown-title"
          >
            <div className="mesh-projects__cycle-summary-head">
              <div>
                <h2 id="cycle-burndown-title" className="mesh-projects__settings-subtitle">
                  {t('analytics.burndown.title')}
                </h2>
                <p className="mesh-projects__sub">
                  {activeCycle.name} · {activeCycle.starts_at} – {activeCycle.ends_at}
                </p>
              </div>
              <span className="mesh-projects__metric-chip">{t('analytics.burndown.points')}</span>
            </div>
            {burndownLoading ? (
              <Skeleton loadingLabel={t('common.loading')} />
            ) : burndownError !== null ? (
              <ErrorState
                title={t('state.errorTitle')}
                description={burndownError}
                retryLabel={t('common.retry')}
                onRetry={() => setBurndownReloadKey((key) => key + 1)}
              />
            ) : burndown !== null ? (
              <div className="mesh-projects__cycle-summary-body">
                <span className="mesh-tnum">
                  {t('analytics.burndown.total', { total: burndown.total })}
                </span>
                <span className="mesh-projects__cycle-remaining mesh-tnum">
                  <span>{t('analytics.burndown.actual')}</span>
                  <strong>{remainingPoints}</strong>
                </span>
                <ProgressBar
                  progress={burndownProgress}
                  title={t('analytics.burndown.chartAria')}
                />
              </div>
            ) : null}
          </section>
        ) : null}

        {workspace === null && !isLoading && error === null ? (
          <EmptyState title={t('state.emptyTitle')} description={t('projects.noWorkspace')} />
        ) : error !== null ? (
          <ErrorState
            title={t('state.errorTitle')}
            description={error}
            retryLabel={t('common.retry')}
            onRetry={() => setReloadKey((key) => key + 1)}
          />
        ) : isLoading ? (
          <Skeleton loadingLabel={t('common.loading')} />
        ) : cycles.length === 0 ? (
          <EmptyState title={t('state.emptyTitle')} description={t('cycles.empty')} />
        ) : (
          <ul className="mesh-projects__cycle-list" data-testid="cycle-list">
            {cycles.map((cycle) => (
              <li
                key={cycle.id}
                className="mesh-projects__cycle-row"
                data-testid={`cycle-row-${cycle.id}`}
              >
                <div className="mesh-projects__cycle-info">
                  <span className="mesh-projects__cycle-name">{cycle.name}</span>
                  <span className="mesh-projects__cycle-sub">
                    {cycle.starts_at} – {cycle.ends_at}
                    {cycle.auto_roll ? ` · ${t('cycles.autoRollTag')}` : ''}
                  </span>
                </div>
                <span
                  className={`mesh-projects__cycle-state mesh-projects__cycle-state--${cycle.state}`}
                >
                  {t(`cycles.state.${cycle.state}`)}
                </span>
                <div className="mesh-projects__milestone-actions">
                  {cycle.state === 'planned' ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      data-testid={`cycle-activate-${cycle.id}`}
                      onClick={() => void advance(cycle, 'active')}
                    >
                      {t('cycles.action.activate')}
                    </Button>
                  ) : null}
                  {cycle.state === 'active' ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      data-testid={`cycle-complete-${cycle.id}`}
                      onClick={() => void advance(cycle, 'completed')}
                    >
                      {t('cycles.action.complete')}
                    </Button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </DataView>

      {workspace !== null ? (
        <CreateCycleDialog
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          client={client}
          workspaceId={workspace.workspace_id}
          onCreated={(cycle) => setCycles((prev) => [cycle, ...prev])}
        />
      ) : null}
    </main>
  );
}
