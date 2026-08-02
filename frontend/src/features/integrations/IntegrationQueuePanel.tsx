/** Authorized conversation queue view (integrations.md §3.9 / §4.2). */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useIntl } from 'react-intl';
import { Link } from 'react-router';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import {
  Banner,
  Button,
  EmptyState,
  ErrorState,
  Skeleton,
  StatusDot,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import {
  cancelQueueItem,
  getQueueSummary,
  listExternalIdentities,
  listQueueAudit,
  listQueueItems,
} from './api';
import {
  conversationDisplayName,
  externalIdentityTriple,
  formatQueueDuration,
  formatRelativeTime,
  QUEUE_STATE_TONE,
  sanitizeMessageExcerpt,
} from './format';
import type {
  ExternalIdentity,
  QueueAuditItem,
  QueueConversationSummary,
  QueueItem,
} from './types';
import './integrations.css';

const PAGE_LIMIT = 100;
const POLL_INTERVAL_MS = 4000;

function newClient(): MeshApiClient {
  return new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
}

export interface QueueRefreshRequest {
  readonly key: number;
  /** null means a project invalidation withheld its key; otherwise every queued workspace key is refetched. */
  readonly conversationKeys: ReadonlyArray<string> | null;
}

export interface IntegrationQueuePanelProps {
  readonly workspaceId: string;
  readonly integrationId: string;
  readonly isAdmin: boolean;
  readonly realtimeConnected: boolean;
  readonly refreshRequest: QueueRefreshRequest;
  readonly onRefreshConsumed?: (key: number) => void;
}

interface ConversationGroup {
  readonly key: string;
  readonly items: ReadonlyArray<QueueItem>;
  readonly summary: QueueConversationSummary | null;
}

interface QueueLoadOptions {
  readonly cursor?: string;
  readonly append?: boolean;
}

export function IntegrationQueuePanel(props: IntegrationQueuePanelProps): React.JSX.Element {
  const {
    workspaceId,
    integrationId,
    isAdmin,
    realtimeConnected,
    refreshRequest,
    onRefreshConsumed,
  } = props;
  const t = useT();
  const intl = useIntl();
  const toast = useToast();
  const mountedRef = useRef(true);
  const loadChainRef = useRef<Promise<void>>(Promise.resolve());
  const pendingLoadCountRef = useRef(0);
  const pendingRefreshKeysRef = useRef<Set<string>>(new Set());
  const fullRefreshPendingRef = useRef(false);
  const latestRefreshKeyRef = useRef(0);
  const refreshDrainRunningRef = useRef(false);
  const drainRefreshesRef = useRef<() => void>(() => undefined);
  const auditGenerationRef = useRef(0);
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<QueueConversationSummary[]>([]);
  const [identities, setIdentities] = useState<ExternalIdentity[]>([]);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [initialLoadComplete, setInitialLoadComplete] = useState(false);
  const [clockMs, setClockMs] = useState(Date.now());
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);
  const [auditItems, setAuditItems] = useState<QueueAuditItem[] | null>(null);
  const [auditNextCursor, setAuditNextCursor] = useState<string | null>(null);
  const [auditErrorKey, setAuditErrorKey] = useState<string | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const load = useCallback(
    (conversationKey: string | null, options: QueueLoadOptions = {}): Promise<boolean> => {
      pendingLoadCountRef.current += 1;
      if (mountedRef.current) setLoading(true);
      const run = async (): Promise<boolean> => {
        try {
          const client = newClient();
          const [listing, summaryListing, identityListing] = await Promise.all([
            listQueueItems(client, workspaceId, integrationId, {
              conversationKey: conversationKey ?? undefined,
              cursor: options.cursor,
              limit: PAGE_LIMIT,
            }),
            getQueueSummary(client, workspaceId, integrationId),
            listExternalIdentities(client, workspaceId),
          ]);
          if (!mountedRef.current) return false;
          setItems((current) => {
            if (options.append && current !== null) {
              const incomingIds = new Set(listing.data.map((item) => item.id));
              return [...current.filter((item) => !incomingIds.has(item.id)), ...listing.data];
            }
            if (conversationKey === null || current === null) return listing.data;
            return [
              ...current.filter((item) => item.conversation_key !== conversationKey),
              ...listing.data,
            ];
          });
          setSummaries(summaryListing.data);
          setIdentities(identityListing.data);
          if (conversationKey === null) setNextCursor(listing.nextCursor);
          setErrorKey(null);
          return true;
        } catch (error) {
          if (!mountedRef.current) return false;
          setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
          return false;
        } finally {
          pendingLoadCountRef.current -= 1;
          if (mountedRef.current && pendingLoadCountRef.current === 0) setLoading(false);
        }
      };
      const result = loadChainRef.current.then(run, run);
      loadChainRef.current = result.then(
        () => undefined,
        () => undefined,
      );
      return result;
    },
    [workspaceId, integrationId],
  );

  useEffect(() => {
    let cancelled = false;
    void load(null).then((applied) => {
      if (!cancelled && applied && mountedRef.current) setInitialLoadComplete(true);
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const drainRefreshes = useCallback((): void => {
    if (
      refreshDrainRunningRef.current ||
      (!fullRefreshPendingRef.current && pendingRefreshKeysRef.current.size === 0)
    ) {
      return;
    }
    refreshDrainRunningRef.current = true;
    void (async () => {
      try {
        while (mountedRef.current) {
          const fullRefresh = fullRefreshPendingRef.current;
          const conversationKeys: ReadonlyArray<string | null> = fullRefresh
            ? [null]
            : [...pendingRefreshKeysRef.current];
          if (conversationKeys.length === 0) return;
          const consumedThroughKey = latestRefreshKeyRef.current;
          fullRefreshPendingRef.current = false;
          pendingRefreshKeysRef.current.clear();

          let applied = true;
          for (const conversationKey of conversationKeys) {
            if (!(await load(conversationKey))) {
              applied = false;
              break;
            }
          }
          if (!applied) {
            if (fullRefresh || fullRefreshPendingRef.current) {
              fullRefreshPendingRef.current = true;
              pendingRefreshKeysRef.current.clear();
            } else {
              for (const conversationKey of conversationKeys) {
                if (conversationKey !== null) pendingRefreshKeysRef.current.add(conversationKey);
              }
            }
            return;
          }
          onRefreshConsumed?.(consumedThroughKey);
        }
      } finally {
        refreshDrainRunningRef.current = false;
      }
    })();
  }, [load, onRefreshConsumed]);

  useEffect(() => {
    drainRefreshesRef.current = drainRefreshes;
  }, [drainRefreshes]);

  useEffect(() => {
    if (!initialLoadComplete) return;
    latestRefreshKeyRef.current = refreshRequest.key;
    if (refreshRequest.conversationKeys === null) {
      fullRefreshPendingRef.current = true;
      pendingRefreshKeysRef.current.clear();
    } else if (!fullRefreshPendingRef.current) {
      for (const conversationKey of refreshRequest.conversationKeys) {
        pendingRefreshKeysRef.current.add(conversationKey);
      }
    }
    drainRefreshesRef.current();
  }, [initialLoadComplete, refreshRequest]);

  useEffect(() => {
    if (realtimeConnected || !initialLoadComplete) return;
    const interval = window.setInterval(() => {
      if (pendingLoadCountRef.current === 0) void load(null);
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [realtimeConnected, initialLoadComplete, load]);

  const hasRunningItem = items?.some(
    (item) => item.started_at !== null && item.finished_at === null,
  );
  useEffect(() => {
    if (!hasRunningItem) return;
    const interval = window.setInterval(() => setClockMs(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [hasRunningItem]);

  const ownIdentityKeys = useMemo(
    () => new Set(identities.map((identity) => externalIdentityTriple(identity))),
    [identities],
  );

  const groups = useMemo<ReadonlyArray<ConversationGroup>>(() => {
    const byKey = new Map<string, QueueItem[]>();
    for (const item of items ?? []) {
      const group = byKey.get(item.conversation_key) ?? [];
      group.push(item);
      byKey.set(item.conversation_key, group);
    }
    for (const summary of summaries) {
      if (!byKey.has(summary.conversation_key)) byKey.set(summary.conversation_key, []);
    }
    const summaryByKey = new Map(summaries.map((summary) => [summary.conversation_key, summary]));
    return [...byKey.entries()]
      .map(([key, groupItems]) => ({
        key,
        items: [...groupItems].sort((left, right) => left.seq - right.seq),
        summary: summaryByKey.get(key) ?? null,
      }))
      .sort((left, right) => left.key.localeCompare(right.key));
  }, [items, summaries]);

  const cancelItem = useCallback(
    async (item: QueueItem): Promise<void> => {
      setCancellingId(item.id);
      try {
        await cancelQueueItem(newClient(), workspaceId, integrationId, item.id);
        toast.addToast(t('integrations.queue.cancelledToast'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
        await load(item.conversation_key);
      } catch (error) {
        toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
      } finally {
        if (mountedRef.current) setCancellingId(null);
      }
    },
    [workspaceId, integrationId, toast, t, load],
  );

  const loadAudit = useCallback(
    async (cursor?: string, append = false): Promise<void> => {
      const generation = ++auditGenerationRef.current;
      setAuditLoading(true);
      try {
        const listing = await listQueueAudit(newClient(), workspaceId, {
          cursor,
          limit: PAGE_LIMIT,
        });
        if (!mountedRef.current || generation !== auditGenerationRef.current) return;
        setAuditItems((current) => {
          if (!append || current === null) return listing.data;
          const incomingIds = new Set(listing.data.map((item) => item.id));
          return [...current.filter((item) => !incomingIds.has(item.id)), ...listing.data];
        });
        setAuditNextCursor(listing.nextCursor);
        setAuditErrorKey(null);
      } catch (error) {
        if (!mountedRef.current || generation !== auditGenerationRef.current) return;
        setAuditErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
      } finally {
        if (mountedRef.current && generation === auditGenerationRef.current) setAuditLoading(false);
      }
    },
    [workspaceId],
  );

  const openAudit = useCallback((): void => {
    if (!isAdmin) return;
    setAuditOpen(true);
    setAuditItems(null);
    setAuditNextCursor(null);
    setAuditErrorKey(null);
    void loadAudit();
  }, [isAdmin, loadAudit]);

  const retryFullLoad = useCallback(async (): Promise<void> => {
    const applied = await load(null);
    if (applied && mountedRef.current) {
      setInitialLoadComplete(true);
      drainRefreshesRef.current();
    }
  }, [load]);

  if (loading && items === null) {
    return <Skeleton loadingLabel={t('integrations.queue.loading')} />;
  }

  if (errorKey !== null && items === null) {
    return (
      <ErrorState
        title={t(errorKey)}
        retryLabel={t('common.retry')}
        onRetry={() => void retryFullLoad()}
      />
    );
  }

  return (
    <div className="mesh-integrations__queue" data-testid="integration-queue-panel">
      <div className="mesh-integrations__header">
        <div>
          <h3>{t('integrations.queue.title')}</h3>
          <p className="mesh-integrations__muted">{t('integrations.queue.visibilityHint')}</p>
        </div>
        <div className="mesh-integrations__toolbar">
          <Button variant="secondary" size="sm" onClick={() => void load(null)}>
            {t('common.retry')}
          </Button>
          {isAdmin && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void openAudit()}
              data-testid="queue-audit-open"
            >
              {t('integrations.queue.audit')}
            </Button>
          )}
        </div>
      </div>

      {errorKey !== null && items !== null && (
        <Banner tone="danger" politeness="assertive">
          <span>{t(errorKey)}</span>{' '}
          <Button variant="secondary" size="sm" onClick={() => void retryFullLoad()}>
            {t('common.retry')}
          </Button>
        </Banner>
      )}

      {groups.length === 0 ? (
        <EmptyState
          title={t('integrations.queue.empty')}
          description={t('integrations.queue.emptyDescription')}
        />
      ) : (
        <div className="mesh-integrations__queue-groups">
          {groups.map((group) => (
            <section
              key={group.key}
              className="mesh-integrations__queue-conversation"
              data-testid={`queue-conversation-${group.key}`}
            >
              <div className="mesh-integrations__header">
                <div>
                  <h4>{conversationDisplayName(group.key)}</h4>
                  <code className="mesh-integrations__muted">{group.key}</code>
                </div>
                <span className="mesh-integrations__tag">
                  {t('integrations.queue.pendingCount', {
                    count:
                      group.summary?.pending_count ??
                      group.items.filter((item) => item.state === 'pending').length,
                  })}
                </span>
              </div>
              <ol className="mesh-integrations__queue-items">
                {group.items.map((item) => {
                  const canManageItem = isAdmin || ownIdentityKeys.has(item.sender.identity_key);
                  const cancellable = item.state === 'pending';
                  const inFlight = ['dispatching', 'processing', 'cancelling'].includes(item.state);
                  const duration =
                    item.started_at === null
                      ? null
                      : formatQueueDuration(item.started_at, item.finished_at, clockMs);
                  const stopHintId = `queue-stop-hint-${item.id}`;
                  const senderDisplay =
                    !item.sender.linked && item.sender.display_name === item.sender.identity_key
                      ? t('integrations.queue.externalSender')
                      : item.sender.display_name;
                  return (
                    <li
                      key={item.id}
                      className="mesh-integrations__queue-item"
                      data-testid={`queue-item-${item.id}`}
                    >
                      <div className="mesh-integrations__queue-item-head">
                        <StatusDot
                          tone={QUEUE_STATE_TONE[item.state]}
                          label={t(`integrations.queue.state.${item.state}`)}
                        />
                        {item.position !== null && (
                          <span
                            className="mesh-integrations__tag"
                            data-testid={`queue-position-${item.id}`}
                          >
                            {t('integrations.queue.position', { position: item.position })}
                          </span>
                        )}
                      </div>
                      <p className="mesh-integrations__queue-excerpt">
                        {sanitizeMessageExcerpt(item.message_excerpt)}
                      </p>
                      <div className="mesh-integrations__queue-meta">
                        <span>
                          {t('integrations.queue.sender')}: {senderDisplay}
                          {!item.sender.linked && (
                            <span className="mesh-integrations__tag">
                              {t('integrations.queue.notLinked')}
                            </span>
                          )}
                        </span>
                        {item.target_agent !== null && (
                          <span>
                            {t('integrations.queue.target')}: {item.target_agent.name}{' '}
                            <span className="mesh-integrations__tag">
                              {t('integrations.queue.ai')}
                            </span>
                          </span>
                        )}
                        <span>
                          {t('integrations.queue.enqueued')}:{' '}
                          {formatRelativeTime(item.enqueued_at, Date.now(), intl.locale) ??
                            item.enqueued_at}
                        </span>
                        {duration !== null && (
                          <span data-testid={`queue-duration-${item.id}`}>
                            {t('integrations.queue.duration')}: {duration}
                          </span>
                        )}
                        {item.ack_sent_at !== null && (
                          <span>{t('integrations.queue.ackSent')}</span>
                        )}
                        {item.ack_merged_into !== null && (
                          <span>{t('integrations.queue.ackMerged')}</span>
                        )}
                      </div>
                      <div className="mesh-integrations__toolbar">
                        {item.execution_id !== null && (
                          <Link
                            to={`/executions/${item.execution_id}`}
                            data-testid={`queue-execution-${item.id}`}
                          >
                            {t('integrations.queue.viewExecution')}
                          </Link>
                        )}
                        {canManageItem && (
                          <Button
                            variant="secondary"
                            size="sm"
                            isLoading={cancellingId === item.id}
                            disabled={!cancellable}
                            title={inFlight ? t('integrations.queue.useStop') : undefined}
                            aria-describedby={inFlight ? stopHintId : undefined}
                            onClick={() => void cancelItem(item)}
                            data-testid={`queue-cancel-${item.id}`}
                          >
                            {t('integrations.queue.cancel')}
                          </Button>
                        )}
                        {canManageItem && inFlight && (
                          <span id={stopHintId} className="mesh-integrations__muted">
                            {t('integrations.queue.useStop')}
                          </span>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ol>
            </section>
          ))}
        </div>
      )}

      {nextCursor !== null && (
        <Button
          variant="secondary"
          isLoading={loading}
          onClick={() => void load(null, { cursor: nextCursor, append: true })}
          data-testid="queue-load-more"
        >
          {t('integrations.queue.loadMore')}
        </Button>
      )}

      {auditOpen && (
        <section className="mesh-integrations__section" data-testid="queue-audit-panel">
          <div className="mesh-integrations__header">
            <h3>{t('integrations.queue.audit')}</h3>
            <Button variant="ghost" size="sm" onClick={() => setAuditOpen(false)}>
              {t('common.close')}
            </Button>
          </div>
          <p className="mesh-integrations__muted">{t('integrations.queue.auditHint')}</p>
          {auditLoading && auditItems === null && (
            <Skeleton loadingLabel={t('integrations.queue.auditLoading')} />
          )}
          {auditErrorKey !== null && (
            <ErrorState
              title={t(auditErrorKey)}
              retryLabel={t('common.retry')}
              onRetry={() => void loadAudit()}
            />
          )}
          {auditItems !== null && auditItems.length === 0 && (
            <EmptyState title={t('integrations.queue.auditEmpty')} description="" />
          )}
          {auditItems !== null && auditItems.length > 0 && (
            <ul className="mesh-integrations__timeline">
              {auditItems.map((item) => (
                <li
                  key={item.id}
                  className="mesh-integrations__timeline-item"
                  data-testid={`queue-audit-${item.id}`}
                >
                  <strong>{item.binding_display}</strong>
                  <span>{sanitizeMessageExcerpt(item.message_excerpt)}</span>
                  <StatusDot
                    tone={QUEUE_STATE_TONE[item.state]}
                    label={t(`integrations.queue.state.${item.state}`)}
                  />
                </li>
              ))}
            </ul>
          )}
          {auditNextCursor !== null && (
            <Button
              variant="secondary"
              isLoading={auditLoading}
              onClick={() => void loadAudit(auditNextCursor, true)}
              data-testid="queue-audit-load-more"
            >
              {t('integrations.queue.loadMore')}
            </Button>
          )}
        </section>
      )}
    </div>
  );
}
