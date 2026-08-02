/**
 * 入站事件台账(integrations.md §4.2 / §3.2):时间 + 事件类型 + 签名状态徽章
 * (valid/invalid/missing)+ 处理状态徽章(received/matched/dispatched/deduped/
 * rejected/processed/failed)+ 可展开载荷预览(只读 JSON,外部内容标注「不可信数据」
 * §6.15)。rejected/deduped 行高亮并给出原因,直接回答「为什么没触发」。签名/处理
 * 状态过滤;父级经 reloadKey 驱动实时重拉(workspace 频道 integration.event_ingested)。
 */
import { useEffect, useState } from 'react';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import { Banner, EmptyState, ErrorState, Select, Skeleton, StatusDot } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { listBindings, listIntegrationEvents } from './api';
import { PROCESS_STATUS_TONE, SIGNATURE_STATUS_TONE, formatRelativeTime } from './format';
import type { IntegrationEvent } from './types';
import './integrations.css';

const STATUS_ALL = 'all';
const PAGE_LIMIT = 50;
// Leave half of the gateway's default 256-channel budget for shell and other
// page subscriptions. Overflow and channel errors fall back to REST polling.
const MAX_PROJECT_REALTIME_SUBSCRIPTIONS = 128;

function newClient(): MeshApiClient {
  return new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
}

function rowModifier(event: IntegrationEvent): string | null {
  if (event.process_status === 'rejected') return 'mesh-integrations__row--rejected';
  if (event.process_status === 'deduped') return 'mesh-integrations__row--deduped';
  return null;
}

export interface EventLedgerProps {
  readonly workspaceId: string;
  readonly integrationId: string;
  readonly reloadKey?: number;
}

export function EventLedger(props: EventLedgerProps): React.JSX.Element {
  const { workspaceId, integrationId, reloadKey = 0 } = props;
  const t = useT();
  const realtime = useRealtimeContext();
  const [events, setEvents] = useState<IntegrationEvent[] | null>(null);
  const [eventProjectIds, setEventProjectIds] = useState<readonly string[]>([]);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [localReloadKey, setLocalReloadKey] = useState(0);
  const [signatureFilter, setSignatureFilter] = useState(STATUS_ALL);
  const [processFilter, setProcessFilter] = useState(STATUS_ALL);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const client = newClient();
    void (async () => {
      try {
        const listing = await listIntegrationEvents(client, workspaceId, integrationId, {
          signature_status: signatureFilter === STATUS_ALL ? undefined : signatureFilter,
          process_status: processFilter === STATUS_ALL ? undefined : processFilter,
          limit: PAGE_LIMIT,
        });
        if (cancelled) return;
        setEvents(listing.data);
        setErrorKey(null);
      } catch (error) {
        if (cancelled) return;
        setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
        setEvents(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId, integrationId, signatureFilter, processFilter, reloadKey, localReloadKey]);

  useEffect(() => {
    let cancelled = false;
    void listBindings(newClient(), workspaceId, integrationId)
      .then((listing) => {
        if (cancelled) return;
        setEventProjectIds(
          [
            ...new Set(
              listing.data.flatMap((binding) =>
                binding.scope === 'project' && binding.project_id !== null
                  ? [binding.project_id]
                  : [],
              ),
            ),
          ].sort(),
        );
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [workspaceId, integrationId, reloadKey, localReloadKey]);

  useEffect(() => {
    if (realtime === null || eventProjectIds.length === 0) return;
    const channels = eventProjectIds
      .slice(0, MAX_PROJECT_REALTIME_SUBSCRIPTIONS)
      .map((projectId) => `project:${projectId}`);
    const channelSet = new Set(channels);
    for (const channel of channels) realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (
        channelSet.has(frame.channel) &&
        frame.event === 'integration.event_ingested' &&
        frame.payload.integration_id === integrationId
      ) {
        setLocalReloadKey((key) => key + 1);
      }
    });
    return () => {
      unsubscribe();
      for (const channel of channels) realtime.client.unsubscribe(channel);
    };
  }, [realtime, eventProjectIds, integrationId]);

  useEffect(() => {
    // Realtime frames are an acceleration path, not the source of truth.
    // Unknown/rejected callbacks intentionally have no attributable frame,
    // so a low-frequency REST read must continue even on a healthy socket.
    const interval = window.setInterval(() => {
      setLocalReloadKey((key) => key + 1);
    }, env.pollingIntervalMs);
    return () => window.clearInterval(interval);
  }, []);

  const nowMs = Date.now();
  const locale = navigator.language;

  return (
    <div data-testid="event-ledger">
      <div className="mesh-integrations__toolbar">
        <Select
          label={t('integrations.events.signatureFilter')}
          value={signatureFilter}
          onChange={(event) => setSignatureFilter(event.target.value)}
          data-testid="event-filter-signature"
        >
          <option value={STATUS_ALL}>{t('integrations.filters.all')}</option>
          <option value="valid">{t('integrations.signature.valid')}</option>
          <option value="invalid">{t('integrations.signature.invalid')}</option>
          <option value="missing">{t('integrations.signature.missing')}</option>
        </Select>
        <Select
          label={t('integrations.events.processFilter')}
          value={processFilter}
          onChange={(event) => setProcessFilter(event.target.value)}
          data-testid="event-filter-process"
        >
          <option value={STATUS_ALL}>{t('integrations.filters.all')}</option>
          <option value="received">{t('integrations.process.received')}</option>
          <option value="matched">{t('integrations.process.matched')}</option>
          <option value="dispatched">{t('integrations.process.dispatched')}</option>
          <option value="deduped">{t('integrations.process.deduped')}</option>
          <option value="rejected">{t('integrations.process.rejected')}</option>
          <option value="processed">{t('integrations.process.processed')}</option>
          <option value="failed">{t('integrations.process.failed')}</option>
        </Select>
      </div>

      {errorKey !== null && (
        <ErrorState
          title={t(errorKey)}
          retryLabel={t('common.retry')}
          onRetry={() => setLocalReloadKey((key) => key + 1)}
        />
      )}
      {events === null && errorKey === null && (
        <Skeleton loadingLabel={t('integrations.loading')} />
      )}
      {events !== null && events.length === 0 && (
        <EmptyState title={t('integrations.events.empty')} description="" />
      )}
      {events !== null && events.length > 0 && (
        <table className="mesh-integrations__table" data-testid="event-table">
          <thead>
            <tr>
              <th>{t('integrations.events.time')}</th>
              <th>{t('integrations.events.type')}</th>
              <th>{t('integrations.events.signature')}</th>
              <th>{t('integrations.events.process')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {events.map((event) => {
              const modifier = rowModifier(event);
              const expanded = expandedId === event.id;
              return (
                <FragmentRow
                  key={event.id}
                  event={event}
                  modifier={modifier}
                  expanded={expanded}
                  timeLabel={
                    formatRelativeTime(event.received_at, nowMs, locale) ?? event.received_at
                  }
                  onToggle={() => setExpandedId(expanded ? null : event.id)}
                />
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

interface FragmentRowProps {
  readonly event: IntegrationEvent;
  readonly modifier: string | null;
  readonly expanded: boolean;
  readonly timeLabel: string;
  readonly onToggle: () => void;
}

function FragmentRow(props: FragmentRowProps): React.JSX.Element {
  const { event, modifier, expanded, timeLabel, onToggle } = props;
  const t = useT();
  const showReason = event.process_status === 'rejected' || event.process_status === 'deduped';
  return (
    <>
      <tr
        className={modifier ?? undefined}
        data-testid={`event-row-${event.id}`}
        onClick={onToggle}
      >
        <td>{timeLabel}</td>
        <td>{event.event_type}</td>
        <td>
          <StatusDot
            tone={SIGNATURE_STATUS_TONE[event.signature_status]}
            label={t(`integrations.signature.${event.signature_status}`)}
          />
        </td>
        <td>
          <StatusDot
            tone={PROCESS_STATUS_TONE[event.process_status]}
            label={t(`integrations.process.${event.process_status}`)}
          />
          {showReason ? (
            <span className="mesh-integrations__muted" data-testid={`event-reason-${event.id}`}>
              {' '}
              {t(`integrations.events.reason.${event.process_status}`)}
            </span>
          ) : null}
        </td>
        <td>
          <button type="button" onClick={onToggle} data-testid={`event-toggle-${event.id}`}>
            {expanded ? '−' : '+'}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr data-testid={`event-payload-row-${event.id}`}>
          <td colSpan={5}>
            <Banner tone="warn">
              <strong>{t('integrations.events.untrusted')}</strong>
            </Banner>
            <pre className="mesh-integrations__json mesh-integrations__payload">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}
