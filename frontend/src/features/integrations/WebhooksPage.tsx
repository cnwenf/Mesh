/**
 * 出向 Webhook 订阅页(integrations.md §4.1 / §3.4):订阅表(https URL / 事件过滤
 * chips / 状态徽章,disabled+fail_count>0 = 熔断)+ 创建 Dialog(https 预检 +
 * 事件类型多选;201 后**仅显示一次**签名密钥:复制 + 妥善保存提示,关闭即永久丢失)。
 * 订阅详情:投递时间线(state 图标 / attempts / response_status / next_retry 倒计时 /
 * last_error)+ [手动重试](failed 行);熔断横幅「已连续失败 N 次,已停用 [恢复]」;
 * paused [启用]。RBAC 呈现级:非 admin 只读。
 */
import { useCallback, useEffect, useState } from 'react';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import { Banner, Button, Dialog, EmptyState, ErrorState, Input, Skeleton, StatusDot, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { canViewSettings } from '../../workspace/permissions';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import {
  createSubscription,
  deleteSubscription,
  listDeliveries,
  listSubscriptions,
  patchSubscription,
  resumeSubscription,
  retryDelivery,
  sendSubscriptionTestEvent,
} from './api';
import {
  DELIVERY_STATE_TONE,
  SUBSCRIPTION_STATUS_TONE,
  formatRelativeTime,
  formatSuccessRate,
  isHttpsUrl,
  isTripped,
} from './format';
import type { Delivery, WebhookSubscription } from './types';
import './integrations.css';

function newClient(): MeshApiClient {
  return new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
}

function parseEventTypes(value: string): string[] {
  return value
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry !== '');
}

interface DeliveryTimelineProps {
  readonly deliveries: Delivery[];
  readonly isAdmin: boolean;
  readonly nowMs: number;
  readonly locale: string;
  readonly onRetry: (delivery: Delivery) => void;
}

function DeliveryTimeline(props: DeliveryTimelineProps): React.JSX.Element {
  const { deliveries, isAdmin, nowMs, locale, onRetry } = props;
  const t = useT();
  if (deliveries.length === 0) {
    return <p className="mesh-integrations__muted" data-testid="deliveries-empty">{t('integrations.webhooks.deliveriesEmpty')}</p>;
  }
  return (
    <ul className="mesh-integrations__timeline" data-testid="delivery-timeline">
      {deliveries.map((delivery) => (
        <li key={delivery.id} className="mesh-integrations__timeline-item" data-testid={`delivery-row-${delivery.id}`}>
          <div className="mesh-integrations__timeline-head">
            <StatusDot
              tone={DELIVERY_STATE_TONE[delivery.state]}
              label={t(`integrations.webhooks.deliveryState.${delivery.state}`)}
            />
            <span className="mesh-integrations__muted">
              {t('integrations.webhooks.attempts', { count: delivery.attempts })}
              {delivery.response_status !== null ? ` · HTTP ${delivery.response_status}` : ''}
            </span>
          </div>
          <span className="mesh-integrations__muted">{delivery.event_ref}</span>
          {delivery.state === 'pending' && delivery.next_retry_at !== null && (
            <span className="mesh-integrations__muted" data-testid={`delivery-retry-${delivery.id}`}>
              {t('integrations.webhooks.nextRetry')}{' '}
              {formatRelativeTime(delivery.next_retry_at, nowMs, locale)}
            </span>
          )}
          {delivery.last_error !== null && (
            <span className="mesh-integrations__muted" data-testid={`delivery-error-${delivery.id}`}>
              {delivery.last_error}
            </span>
          )}
          {delivery.state === 'failed' && isAdmin && (
            <Button variant="secondary" size="sm" onClick={() => onRetry(delivery)} data-testid={`delivery-retry-btn-${delivery.id}`}>
              {t('integrations.webhooks.retry')}
            </Button>
          )}
        </li>
      ))}
    </ul>
  );
}

export function WebhooksPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();

  const [membership, setMembership] = useState<Membership | null>(null);
  const [subscriptions, setSubscriptions] = useState<WebhookSubscription[] | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [eventTypes, setEventTypes] = useState('');
  const [busy, setBusy] = useState(false);
  const [freshSecret, setFreshSecret] = useState<WebhookSubscription | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[] | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [sendTestId, setSendTestId] = useState<string | null>(null);

  const isAdmin = membership !== null && canViewSettings(membership.role);

  useEffect(() => {
    let cancelled = false;
    const client = newClient();
    void (async () => {
      try {
        const me = await fetchMe(client);
        const workspace = activeWorkspace(me.memberships);
        if (cancelled) return;
        if (workspace === null) {
          setMembership(null);
          setSubscriptions([]);
          return;
        }
        setMembership(workspace);
        const listing = await listSubscriptions(client, workspace.workspace_id);
        if (cancelled) return;
        setSubscriptions(listing.data);
        setErrorKey(null);
      } catch (error) {
        if (cancelled) return;
        setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
        setSubscriptions(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const loadDeliveries = useCallback(
    async (subscriptionId: string): Promise<void> => {
      if (membership === null) return;
      try {
        const listing = await listDeliveries(newClient(), membership.workspace_id, subscriptionId, { limit: 30 });
        setDeliveries(listing.data);
      } catch {
        setDeliveries([]);
      }
    },
    [membership],
  );

  const toggleExpand = useCallback(
    (subscriptionId: string): void => {
      if (expandedId === subscriptionId) {
        setExpandedId(null);
        setDeliveries(null);
        return;
      }
      setExpandedId(subscriptionId);
      setDeliveries(null);
      void loadDeliveries(subscriptionId);
    },
    [expandedId, loadDeliveries],
  );

  const runAction = useCallback(
    async (action: () => Promise<unknown>, successMessage: string) => {
      try {
        await action();
        toast.addToast(successMessage, { tone: 'success', closeLabel: t('common.close') });
        setReloadKey((key) => key + 1);
      } catch (error) {
        toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
      }
    },
    [toast, t],
  );

  const submitCreate = useCallback(async (): Promise<void> => {
    if (membership === null || !isHttpsUrl(url)) return;
    setBusy(true);
    try {
      const created = await createSubscription(newClient(), membership.workspace_id, {
        url: url.trim(),
        event_types: parseEventTypes(eventTypes),
      });
      setFreshSecret(created);
      setCreateOpen(false);
      setUrl('');
      setEventTypes('');
      setReloadKey((key) => key + 1);
    } catch (error) {
      toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setBusy(false);
    }
  }, [membership, url, eventTypes, toast, t]);

  const copySecret = useCallback(async (): Promise<void> => {
    if (freshSecret === null || freshSecret.secret === undefined) return;
    try {
      await navigator.clipboard.writeText(freshSecret.secret);
      toast.addToast(t('integrations.webhooks.copiedToast'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch {
      toast.addToast(t('integrations.webhooks.copyFailed'), {
        tone: 'warn',
        closeLabel: t('common.close'),
      });
    }
  }, [freshSecret, toast, t]);

  const handleRetry = useCallback(
    (delivery: Delivery): void => {
      if (membership === null) return;
      void runAction(
        () => retryDelivery(newClient(), membership.workspace_id, delivery.subscription_id, delivery.id),
        t('integrations.webhooks.retriedToast'),
      ).then(() => void loadDeliveries(delivery.subscription_id));
    },
    [membership, runAction, loadDeliveries, t],
  );

  const resume = useCallback(
    (subscription: WebhookSubscription): void => {
      if (membership === null) return;
      void runAction(
        () => resumeSubscription(newClient(), membership.workspace_id, subscription.id),
        t('integrations.webhooks.resumedToast'),
      );
    },
    [membership, runAction, t],
  );

  const enable = useCallback(
    (subscription: WebhookSubscription): void => {
      if (membership === null) return;
      void runAction(
        () => patchSubscription(newClient(), membership.workspace_id, subscription.id, { status: 'active' }),
        t('integrations.webhooks.enabledToast'),
      );
    },
    [membership, runAction, t],
  );

  const confirmDelete = useCallback(
    (subscriptionId: string): void => {
      if (membership === null) return;
      void runAction(
        () => deleteSubscription(newClient(), membership.workspace_id, subscriptionId),
        t('integrations.webhooks.deletedToast'),
      );
      setConfirmDeleteId(null);
    },
    [membership, runAction, t],
  );

  const sendTest = useCallback(
    (subscription: WebhookSubscription): void => {
      if (membership === null) return;
      setSendTestId(subscription.id);
      void runAction(
        () => sendSubscriptionTestEvent(newClient(), membership.workspace_id, subscription.id),
        t('integrations.webhooks.testSentToast'),
      ).finally(() => setSendTestId(null));
    },
    [membership, runAction, t],
  );

  const nowMs = Date.now();
  const locale = navigator.language;
  const urlInvalid = url !== '' && !isHttpsUrl(url);

  return (
    <div className="mesh-integrations__page" data-testid="webhooks-page">
      <div className="mesh-integrations__header">
        <h1 className="mesh-integrations__title">{t('integrations.webhooks.title')}</h1>
        {isAdmin && (
          <Button variant="primary" onClick={() => setCreateOpen(true)} data-testid="webhook-create">
            {t('integrations.webhooks.create')}
          </Button>
        )}
      </div>

      {!isAdmin && membership !== null && (
        <div data-testid="webhooks-readonly-banner">
          <Banner tone="info">{t('integrations.readonly')}</Banner>
        </div>
      )}

      {errorKey !== null && (
        <ErrorState title={t(errorKey)} retryLabel={t('common.retry')} onRetry={() => setReloadKey((key) => key + 1)} />
      )}
      {subscriptions === null && errorKey === null && <Skeleton loadingLabel={t('integrations.loading')} />}
      {subscriptions !== null && subscriptions.length === 0 && errorKey === null && (
        <EmptyState title={t('integrations.webhooks.empty')} description="" />
      )}
      {subscriptions !== null && subscriptions.length > 0 && (
        <div data-testid="webhooks-list">
          {subscriptions.map((subscription) => {
            const tripped = isTripped(subscription);
            const expanded = expandedId === subscription.id;
            return (
              <div key={subscription.id} className="mesh-integrations__section" data-testid={`webhook-card-${subscription.id}`}>
                <div className="mesh-integrations__header">
                  <span className="mesh-integrations__vcs-ref" data-testid={`webhook-url-${subscription.id}`}>
                    {subscription.url}
                  </span>
                  <StatusDot
                    tone={SUBSCRIPTION_STATUS_TONE[subscription.status]}
                    label={
                      tripped
                        ? t('integrations.webhooks.status.tripped')
                        : t(`integrations.webhooks.status.${subscription.status}`)
                    }
                  />
                </div>
                <div className="mesh-integrations__card-caps">
                  {subscription.event_types.length === 0 ? (
                    <span className="mesh-integrations__tag">{t('integrations.webhooks.allEvents')}</span>
                  ) : (
                    subscription.event_types.map((eventType) => (
                      <span key={eventType} className="mesh-integrations__tag">
                        {eventType}
                      </span>
                    ))
                  )}
                </div>

                <span className="mesh-integrations__muted" data-testid={`webhook-success-rate-${subscription.id}`}>
                  {t('integrations.webhooks.successRate')} {formatSuccessRate(subscription.success_rate)}
                  {' · '}
                  {t('integrations.webhooks.deliveriesTotal', { count: subscription.deliveries_total })}
                </span>

                {tripped && (
                  <div data-testid={`webhook-breaker-${subscription.id}`}>
                    <Banner tone="danger">
                      {t('integrations.webhooks.breaker', { count: subscription.fail_count })}
                      {isAdmin && (
                        <Button variant="secondary" size="sm" onClick={() => resume(subscription)} data-testid={`webhook-resume-${subscription.id}`}>
                          {t('integrations.webhooks.resume')}
                        </Button>
                      )}
                    </Banner>
                  </div>
                )}
                {subscription.status === 'paused' && isAdmin && (
                  <Button variant="secondary" size="sm" onClick={() => enable(subscription)} data-testid={`webhook-enable-${subscription.id}`}>
                    {t('integrations.webhooks.enable')}
                  </Button>
                )}

                <div className="mesh-integrations__toolbar">
                  <Button variant="ghost" size="sm" onClick={() => toggleExpand(subscription.id)} data-testid={`webhook-expand-${subscription.id}`}>
                    {expanded ? t('integrations.webhooks.hideDeliveries') : t('integrations.webhooks.showDeliveries')}
                  </Button>
                  {isAdmin && (
                    <Button
                      variant="secondary"
                      size="sm"
                      isLoading={sendTestId === subscription.id}
                      onClick={() => sendTest(subscription)}
                      data-testid={`webhook-send-test-${subscription.id}`}
                    >
                      {t('integrations.webhooks.sendTest')}
                    </Button>
                  )}
                  {isAdmin && (
                    <Button variant="ghost" size="sm" onClick={() => setConfirmDeleteId(subscription.id)} data-testid={`webhook-delete-${subscription.id}`}>
                      {t('integrations.actions.delete')}
                    </Button>
                  )}
                </div>

                {expanded && (
                  <div data-testid={`webhook-detail-${subscription.id}`}>
                    {deliveries === null ? (
                      <Skeleton loadingLabel={t('integrations.loading')} />
                    ) : (
                      <DeliveryTimeline
                        deliveries={deliveries}
                        isAdmin={isAdmin}
                        nowMs={nowMs}
                        locale={locale}
                        onRetry={handleRetry}
                      />
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title={t('integrations.webhooks.create')}
        closeLabel={t('common.close')}
      >
        <Input
          label={t('integrations.webhooks.urlLabel')}
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          error={urlInvalid ? t('integrations.webhooks.urlInvalid') : undefined}
          hint={t('integrations.webhooks.urlHint')}
          data-testid="webhook-url-input"
        />
        <Input
          label={t('integrations.webhooks.eventTypesLabel')}
          value={eventTypes}
          onChange={(event) => setEventTypes(event.target.value)}
          hint={t('integrations.webhooks.eventTypesHint')}
          data-testid="webhook-event-types"
        />
        <div className="mesh-integrations__footer">
          <Button variant="ghost" onClick={() => setCreateOpen(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            isLoading={busy}
            disabled={!isHttpsUrl(url)}
            onClick={() => void submitCreate()}
            data-testid="webhook-create-submit"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={freshSecret !== null}
        onClose={() => setFreshSecret(null)}
        title={t('integrations.webhooks.secretTitle')}
        closeLabel={t('common.close')}
      >
        <Banner tone="warn">
          <strong>{t('integrations.webhooks.secretShowOnceTitle')}</strong>{' '}
          {t('integrations.webhooks.secretShowOnceBody')}
        </Banner>
        <div className="mesh-integrations__secret-box" data-testid="webhook-fresh-secret-box">
          <code data-testid="webhook-fresh-secret">{freshSecret !== null ? freshSecret.secret : ''}</code>
          <Button variant="secondary" size="sm" onClick={() => void copySecret()} data-testid="webhook-copy-secret">
            {t('integrations.webhooks.copySecret')}
          </Button>
        </div>
        <div className="mesh-integrations__footer">
          <Button variant="primary" onClick={() => setFreshSecret(null)} data-testid="webhook-secret-done">
            {t('integrations.webhooks.secretSaved')}
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={confirmDeleteId !== null}
        onClose={() => setConfirmDeleteId(null)}
        title={t('integrations.delete.dialogTitle')}
        closeLabel={t('common.close')}
      >
        <p>{t('integrations.webhooks.deleteConfirm')}</p>
        <div className="mesh-integrations__footer">
          <Button variant="ghost" onClick={() => setConfirmDeleteId(null)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="danger"
            onClick={() => confirmDeleteId !== null && confirmDelete(confirmDeleteId)}
            data-testid="webhook-delete-confirm"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
