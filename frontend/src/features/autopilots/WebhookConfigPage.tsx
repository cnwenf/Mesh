/**
 * Webhook 配置页(autopilot.md §4.1 / §4.3 流程 C):入站端点 + 签名密钥
 * (创建后仅显示一次,提示妥善保存)+ 密钥轮换(旧 token 立即失效,规则按
 * secret_id 绑定保持可用)。列表面绝不回显 token / secret(§5.3 红线)。
 */
import { useCallback, useEffect, useState } from 'react';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import { Banner, Button, EmptyState, ErrorState, Input, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import {
  createWebhookSecret,
  inboundWebhookUrl,
  listWebhookEvents,
  listWebhookSecrets,
  rotateWebhookSecret,
} from './api';
import type { WebhookEventItem, WebhookSecretCreated, WebhookSecretPublic } from './types';

/** Maps a caught error to its i18n key (API errors carry a stable code). */
function errorKeyOf(error: unknown): string {
  if (error instanceof MeshApiError) return errorToI18nKey(error);
  return 'error.unknown';
}
import './autopilots.css';

export function WebhookConfigPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const [membership, setMembership] = useState<Membership | null>(null);
  const [secrets, setSecrets] = useState<WebhookSecretPublic[] | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [label, setLabel] = useState('default');
  const [busy, setBusy] = useState(false);
  const [freshCredential, setFreshCredential] = useState<WebhookSecretCreated | null>(null);
  const [events, setEvents] = useState<WebhookEventItem[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
    void (async () => {
      try {
        const me = await fetchMe(client);
        const workspace = activeWorkspace(me.memberships);
        if (cancelled) return;
        setMembership(workspace);
        if (workspace === null) {
          setSecrets([]);
          return;
        }
        const [secretListing, eventListing] = await Promise.all([
          listWebhookSecrets(client, workspace.workspace_id),
          listWebhookEvents(client, workspace.workspace_id, { limit: 20 }),
        ]);
        setSecrets(secretListing);
        setEvents(eventListing.data);
        setErrorKey(null);
      } catch (error) {
        if (cancelled) return;
        setErrorKey(errorKeyOf(error));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const createSecret = useCallback(async () => {
    setBusy(true);
    try {
      const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
      const created = await createWebhookSecret(
        client,
        membership!.workspace_id,
        label.trim() || 'default',
      );
      setFreshCredential(created);
      setReloadKey((key) => key + 1);
    } catch (error) {
      toast.addToast(t(errorKeyOf(error)), { tone: 'danger', closeLabel: t('common.close') });
    } finally {
      setBusy(false);
    }
  }, [membership, label, toast, t]);

  const rotateSecret = useCallback(
    async (secretId: string) => {
      setBusy(true);
      try {
        const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
        const rotated = await rotateWebhookSecret(client, membership!.workspace_id, secretId);
        setFreshCredential(rotated);
        setReloadKey((key) => key + 1);
      } catch (error) {
        toast.addToast(t(errorKeyOf(error)), { tone: 'danger', closeLabel: t('common.close') });
      } finally {
        setBusy(false);
      }
    },
    [membership, toast, t],
  );

  return (
    <div className="mesh-autopilots__page" data-testid="webhook-config-page">
      <div className="mesh-autopilots__header">
        <h1 className="mesh-autopilots__title">{t('autopilots.webhook.title')}</h1>
      </div>

      <p>{t('autopilots.webhook.intro')}</p>

      {freshCredential !== null && (
        <div
          className="mesh-autopilots__secret-box"
          data-testid="webhook-fresh-credential"
          aria-labelledby="webhook-fresh-credential-title"
        >
          <Banner tone="warn">
            <strong id="webhook-fresh-credential-title">
              {t('autopilots.webhook.showOnceTitle')}
            </strong>{' '}
            {t('autopilots.webhook.showOnceBody')}
          </Banner>
          <div>
            <strong>{t('autopilots.webhook.urlLabel')}</strong>
            <code data-testid="webhook-fresh-url">{inboundWebhookUrl(freshCredential.token)}</code>
          </div>
          <div>
            <strong>{t('autopilots.webhook.tokenLabel')}</strong>
            <code>{freshCredential.token}</code>
          </div>
          <div>
            <strong>{t('autopilots.webhook.secretLabel')}</strong>
            <code data-testid="webhook-fresh-secret">{freshCredential.secret}</code>
          </div>
          <div>
            <strong>{t('autopilots.webhook.signatureHelp')}</strong>
            <code>{t('autopilots.webhook.signatureFormat')}</code>
          </div>
          <Button variant="secondary" size="sm" onClick={() => setFreshCredential(null)}>
            {t('autopilots.webhook.dismiss')}
          </Button>
        </div>
      )}

      <div className="mesh-autopilots__toolbar">
        <Input
          label={t('autopilots.webhook.labelInput')}
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          data-testid="webhook-label-input"
        />
        <Button
          variant="primary"
          isLoading={busy}
          disabled={freshCredential !== null}
          onClick={() => void createSecret()}
          data-testid="webhook-create-secret"
        >
          {t('autopilots.webhook.create')}
        </Button>
      </div>

      {errorKey !== null && (
        <ErrorState
          title={t(errorKey)}
          retryLabel={t('common.retry')}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      )}
      {secrets === null && errorKey === null && <Skeleton loadingLabel={t('autopilots.loading')} />}
      {secrets !== null && secrets.length === 0 && (
        <EmptyState title={t('autopilots.webhook.empty')} description="" />
      )}
      {secrets !== null && secrets.length > 0 && (
        <table className="mesh-autopilots__runs-table" data-testid="webhook-secrets-table">
          <thead>
            <tr>
              <th>{t('autopilots.webhook.columnLabel')}</th>
              <th>{t('autopilots.webhook.columnStatus')}</th>
              <th>{t('autopilots.webhook.columnCreated')}</th>
              <th>{t('autopilots.columns.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {secrets.map((secret) => (
              <tr key={secret.id} data-testid={`webhook-secret-row-${secret.id}`}>
                <td>{secret.label}</td>
                <td>{secret.status}</td>
                <td>{new Date(secret.created_at).toLocaleString()}</td>
                <td>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={freshCredential !== null}
                    onClick={() => void rotateSecret(secret.id)}
                    data-testid={`webhook-rotate-${secret.id}`}
                  >
                    {t('autopilots.webhook.rotate')}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* §4.1 最近事件:入站审计(签名/处理状态/去重键) */}
      <div className="mesh-autopilots__card">
        <div className="mesh-autopilots__header">
          <h3>{t('autopilots.webhook.recentEvents')}</h3>
          <Button variant="ghost" size="sm" onClick={() => setReloadKey((key) => key + 1)}>
            {t('autopilots.webhook.refresh')}
          </Button>
        </div>
        {events !== null && events.length === 0 && <p>{t('autopilots.webhook.eventsEmpty')}</p>}
        {events !== null && events.length > 0 && (
          <table className="mesh-autopilots__runs-table" data-testid="webhook-events-table">
            <thead>
              <tr>
                <th>{t('autopilots.webhook.eventType')}</th>
                <th>{t('autopilots.webhook.eventSignature')}</th>
                <th>{t('autopilots.webhook.eventProcess')}</th>
                <th>{t('autopilots.webhook.eventReceived')}</th>
                <th>{t('autopilots.webhook.eventKey')}</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id} data-testid={`webhook-event-row-${event.id}`}>
                  <td>{event.event_type}</td>
                  <td>{t(`autopilots.webhook.signature.${event.signature_status}`)}</td>
                  <td>{t(`autopilots.webhook.process.${event.process_status}`)}</td>
                  <td>{new Date(event.received_at).toLocaleString()}</td>
                  <td>{event.idempotency_key}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
