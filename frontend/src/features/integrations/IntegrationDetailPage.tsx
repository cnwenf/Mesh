/**
 * 集成详情页(integrations.md §4.1):① 概览(非密配置只读 + 编辑 + 状态切换 +
 * 凭据轮换;`has_secret` 指示,绝不展示 secret 明文);② 绑定 tab(BindingDrawer);
 * ③ 事件台账 tab(EventLedger)。实时:workspace:{ws}:integrations / integration:{id}
 * 频道 integration.updated 重拉配置、integration.event_ingested 重拉台账(§3.6)。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import {
  Banner,
  Button,
  Dialog,
  ErrorState,
  Icon,
  Input,
  Select,
  Skeleton,
  StatusDot,
  useToast,
} from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { canViewSettings } from '../../workspace/permissions';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import {
  getIntegration,
  integrationAuthorizeUrl,
  integrationChannel,
  patchIntegration,
  rotateIntegrationSecret,
  testIntegration,
  workspaceIntegrationsChannel,
} from './api';
import { BindingDrawer } from './BindingDrawer';
import { DingTalkInteractionGuide } from './DingTalkInteractionGuide';
import { DingTalkOverviewPanel } from './DingTalkOverviewPanel';
import { EventLedger } from './EventLedger';
import { HEALTH_STATE_TONE, INTEGRATION_STATUS_TONE, KIND_ICON, toHealthState } from './format';
import { IntegrationQueuePanel } from './IntegrationQueuePanel';
import type { QueueRefreshRequest } from './IntegrationQueuePanel';
import type { DingTalkReceiveMode, DingTalkVerbosity, Integration } from './types';
import { DINGTALK_DEFAULT_ACK_TEMPLATE, OAUTH_KINDS } from './types';
import './integrations.css';

type TabKey = 'overview' | 'bindings' | 'events' | 'queue';

function newClient(): MeshApiClient {
  return new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
}

export function IntegrationDetailPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const realtime = useRealtimeContext();
  const { integrationId } = useParams<{ integrationId: string }>();

  const [membership, setMembership] = useState<Membership | null>(null);
  const [integration, setIntegration] = useState<Integration | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>('overview');
  const queueTabActiveRef = useRef(false);
  queueTabActiveRef.current = tab === 'queue';
  const [tabReloadKey, setTabReloadKey] = useState(0);
  const [queueRefresh, setQueueRefresh] = useState<QueueRefreshRequest>({
    key: 0,
    conversationKeys: [],
  });
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editConfig, setEditConfig] = useState('');
  const [editDingTalkAppKey, setEditDingTalkAppKey] = useState('');
  const [editDingTalkCorpId, setEditDingTalkCorpId] = useState('');
  const [editDingTalkReceiveMode, setEditDingTalkReceiveMode] =
    useState<DingTalkReceiveMode>('stream');
  const [editDingTalkVerbosity, setEditDingTalkVerbosity] =
    useState<DingTalkVerbosity>('final_only');
  const [editDingTalkAckTemplate, setEditDingTalkAckTemplate] = useState(
    DINGTALK_DEFAULT_ACK_TEMPLATE,
  );
  const [rotateOpen, setRotateOpen] = useState(false);
  const [rotateSecret, setRotateSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);

  const isAdmin = membership !== null && canViewSettings(membership.role);

  const reloadIntegration = useCallback(async (): Promise<void> => {
    if (membership === null || integrationId === undefined) return;
    try {
      const loaded = await getIntegration(newClient(), membership.workspace_id, integrationId);
      setIntegration(loaded);
    } catch {
      // 重拉失败保留既有数据,不打断页面。
    }
  }, [membership, integrationId]);

  useEffect(() => {
    let cancelled = false;
    const client = newClient();
    void (async () => {
      try {
        const me = await fetchMe(client);
        const workspace = activeWorkspace(me.memberships);
        if (cancelled || workspace === null || integrationId === undefined) return;
        setMembership(workspace);
        const loaded = await getIntegration(client, workspace.workspace_id, integrationId);
        if (cancelled) return;
        setIntegration(loaded);
        setErrorKey(null);
      } catch (error) {
        if (cancelled) return;
        setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [integrationId]);

  useEffect(() => {
    if (realtime === null || membership === null || integrationId === undefined) return;
    const channels = [
      workspaceIntegrationsChannel(membership.workspace_id),
      integrationChannel(integrationId),
    ];
    for (const channel of channels) realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (!channels.includes(frame.channel)) return;
      if (frame.event === 'integration.updated') {
        void reloadIntegration();
        setTabReloadKey((key) => key + 1);
      }
      if (frame.event === 'integration.event_ingested') setTabReloadKey((key) => key + 1);
      if (
        frame.event === 'integration.queue_updated' &&
        frame.payload.integration_id === integrationId
      ) {
        const disclosedKey =
          typeof frame.payload.conversation_key === 'string'
            ? frame.payload.conversation_key
            : null;
        setQueueRefresh((current) => ({
          key: current.key + 1,
          conversationKeys:
            !queueTabActiveRef.current || disclosedKey === null || current.conversationKeys === null
              ? null
              : [...new Set([...current.conversationKeys, disclosedKey])],
        }));
      }
    });
    return () => {
      unsubscribe();
      for (const channel of channels) realtime.client.unsubscribe(channel);
    };
  }, [realtime, membership, integrationId, reloadIntegration]);

  const consumeQueueRefresh = useCallback((key: number): void => {
    setQueueRefresh((current) =>
      current.key === key &&
      (current.conversationKeys === null || current.conversationKeys.length > 0)
        ? { key: current.key, conversationKeys: [] }
        : current,
    );
  }, []);

  const openEdit = useCallback((): void => {
    if (integration === null) return;
    setEditName(integration.name);
    setEditConfig(JSON.stringify(integration.config, null, 2));
    setEditDingTalkAppKey(String(integration.config.app_key ?? ''));
    setEditDingTalkCorpId(String(integration.config.corp_id ?? ''));
    setEditDingTalkReceiveMode(integration.config.receive_mode === 'http' ? 'http' : 'stream');
    setEditDingTalkVerbosity(
      integration.config.verbosity === 'progress' ? 'progress' : 'final_only',
    );
    setEditDingTalkAckTemplate(
      String(integration.config.ack_template ?? DINGTALK_DEFAULT_ACK_TEMPLATE),
    );
    setEditOpen(true);
  }, [integration]);

  const submitEdit = useCallback(async (): Promise<void> => {
    if (membership === null || integration === null) return;
    let configObject: Record<string, unknown>;
    if (integration.kind === 'im_dingtalk') {
      const nonSecretConfig = Object.fromEntries(
        Object.entries(integration.config).filter(([key]) => !key.endsWith('_ref')),
      );
      configObject = {
        ...nonSecretConfig,
        app_key: editDingTalkAppKey.trim(),
        corp_id: editDingTalkCorpId.trim(),
        receive_mode: editDingTalkReceiveMode,
        inbound_queue: 'serial_conversation',
        verbosity: editDingTalkVerbosity,
        ack_template: editDingTalkAckTemplate,
      };
    } else {
      try {
        configObject = JSON.parse(editConfig) as Record<string, unknown>;
      } catch {
        toast.addToast(t('integrations.add.invalidConfig'), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
        return;
      }
    }
    setBusy(true);
    try {
      const updated = await patchIntegration(newClient(), membership.workspace_id, integration.id, {
        name: editName.trim(),
        config: configObject,
      });
      setIntegration(updated);
      toast.addToast(t('integrations.detail.savedToast'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setEditOpen(false);
    } catch (error) {
      toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setBusy(false);
    }
  }, [
    membership,
    integration,
    editName,
    editConfig,
    editDingTalkAppKey,
    editDingTalkCorpId,
    editDingTalkReceiveMode,
    editDingTalkVerbosity,
    editDingTalkAckTemplate,
    toast,
    t,
  ]);

  const toggleStatus = useCallback(async (): Promise<void> => {
    if (membership === null || integration === null) return;
    const nextStatus = integration.status === 'active' ? 'disabled' : 'active';
    setBusy(true);
    try {
      const updated = await patchIntegration(newClient(), membership.workspace_id, integration.id, {
        status: nextStatus,
      });
      setIntegration(updated);
      toast.addToast(
        nextStatus === 'active'
          ? t('integrations.toast.enabled')
          : t('integrations.toast.disabled'),
        { tone: 'success', closeLabel: t('common.close') },
      );
    } catch (error) {
      toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setBusy(false);
    }
  }, [membership, integration, toast, t]);

  const submitRotate = useCallback(async (): Promise<void> => {
    if (membership === null || integration === null || rotateSecret.trim() === '') return;
    setBusy(true);
    try {
      const updated = await rotateIntegrationSecret(
        newClient(),
        membership.workspace_id,
        integration.id,
        rotateSecret.trim(),
      );
      setIntegration(updated);
      toast.addToast(t('integrations.detail.rotatedToast'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setRotateOpen(false);
      setRotateSecret('');
    } catch (error) {
      toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setBusy(false);
    }
  }, [membership, integration, rotateSecret, toast, t]);

  const reauthorize = useCallback((): void => {
    if (membership === null || integration === null) return;
    window.location.assign(integrationAuthorizeUrl(membership.workspace_id, integration.kind));
  }, [membership, integration]);

  const runTest = useCallback(async (): Promise<void> => {
    if (membership === null || integration === null) return;
    setTesting(true);
    try {
      const result = await testIntegration(newClient(), membership.workspace_id, integration.id);
      const nextState = toHealthState(result.health_state);
      setIntegration({ ...integration, health_state: nextState, last_error: result.detail });
      toast.addToast(t('integrations.toast.tested'), {
        tone: nextState === 'healthy' ? 'success' : 'warn',
        closeLabel: t('common.close'),
      });
    } catch (error) {
      toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setTesting(false);
    }
  }, [membership, integration, toast, t]);

  if (errorKey !== null) {
    return (
      <div className="mesh-integrations__page">
        <ErrorState
          title={t(errorKey)}
          retryLabel={t('common.retry')}
          onRetry={() => navigate('/integrations')}
        />
      </div>
    );
  }
  if (integration === null) {
    return (
      <div className="mesh-integrations__page">
        <Skeleton loadingLabel={t('integrations.loading')} />
      </div>
    );
  }

  const tabButton = (key: TabKey, label: string): React.JSX.Element => (
    <button
      type="button"
      className={
        tab === key
          ? 'mesh-integrations__tab mesh-integrations__tab--active'
          : 'mesh-integrations__tab'
      }
      onClick={() => {
        if (key === 'queue' && tab !== 'queue') {
          // A full mount load supersedes every bounded invalidation received while the tab was hidden.
          setQueueRefresh((current) => ({ key: current.key, conversationKeys: [] }));
        }
        setTab(key);
      }}
      data-testid={`integration-tab-${key}`}
    >
      {label}
    </button>
  );

  return (
    <div className="mesh-integrations__page" data-testid="integration-detail">
      <div className="mesh-integrations__header">
        <h1 className="mesh-integrations__title" data-testid="integration-detail-name">
          <Icon name={KIND_ICON[integration.kind]} size={16} /> {integration.name}
        </h1>
        <StatusDot
          tone={INTEGRATION_STATUS_TONE[integration.status]}
          label={t(`integrations.status.${integration.status}`)}
        />
        <span data-testid="integration-health" title={integration.last_error ?? undefined}>
          <StatusDot
            tone={HEALTH_STATE_TONE[integration.health_state]}
            label={t(`integrations.health.${integration.health_state}`)}
          />
        </span>
      </div>

      {integration.status === 'disabled' && (
        <div className="mesh-integrations__muted" data-testid="integration-disabled-note">
          {t('integrations.detail.disabledNote')}
        </div>
      )}

      {integration.health_state === 'auth_failed' && (
        <div data-testid="integration-auth-failed-banner">
          <Banner tone="danger">
            {t('integrations.detail.authFailed')}
            {isAdmin && OAUTH_KINDS.has(integration.kind) && (
              <Button
                variant="secondary"
                size="sm"
                onClick={reauthorize}
                data-testid="integration-reauthorize"
              >
                {t('integrations.actions.reauthorize')}
              </Button>
            )}
          </Banner>
          {integration.last_error !== null && (
            <div className="mesh-integrations__muted" data-testid="integration-last-error">
              {integration.last_error}
            </div>
          )}
        </div>
      )}

      <div className="mesh-integrations__tabs">
        {tabButton('overview', t('integrations.tab.overview'))}
        {tabButton('bindings', t('integrations.tab.bindings'))}
        {tabButton('events', t('integrations.tab.events'))}
        {integration.kind === 'im_dingtalk' && tabButton('queue', t('integrations.tab.queue'))}
      </div>

      {tab === 'overview' && (
        <>
          {integration.kind === 'im_dingtalk' && membership !== null && (
            <DingTalkOverviewPanel
              workspaceId={membership.workspace_id}
              integration={integration}
              isAdmin={isAdmin}
              onEdit={openEdit}
              reloadKey={tabReloadKey}
            />
          )}
          <div className="mesh-integrations__section" data-testid="integration-overview">
            <div className="mesh-integrations__header">
              <h3>{t('integrations.detail.configTitle')}</h3>
              {isAdmin && (
                <div className="mesh-integrations__toolbar">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={openEdit}
                    data-testid="integration-edit"
                  >
                    {t('integrations.actions.edit')}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => void toggleStatus()}
                    data-testid="integration-status-toggle"
                  >
                    {integration.status === 'active'
                      ? t('integrations.actions.disable')
                      : t('integrations.actions.enable')}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      setRotateSecret('');
                      setRotateOpen(true);
                    }}
                    data-testid="integration-rotate"
                  >
                    {t('integrations.detail.rotate')}
                  </Button>
                  {integration.kind !== 'im_dingtalk' && (
                    <Button
                      variant="secondary"
                      size="sm"
                      isLoading={testing}
                      onClick={() => void runTest()}
                      data-testid="integration-test"
                    >
                      {t('integrations.actions.test')}
                    </Button>
                  )}
                </div>
              )}
            </div>
            <dl className="mesh-integrations__kv">
              <dt>{t('integrations.columns.kind')}</dt>
              <dd>{t(`integrations.kind.${integration.kind}`)}</dd>
              <dt>{t('integrations.detail.credential')}</dt>
              <dd data-testid="integration-has-secret">
                {integration.has_secret
                  ? t('integrations.detail.hasSecret')
                  : t('integrations.detail.noSecret')}
              </dd>
              <dt>{t('integrations.detail.configTitle')}</dt>
              <dd>
                <pre className="mesh-integrations__json" data-testid="integration-config">
                  {JSON.stringify(integration.config, null, 2)}
                </pre>
              </dd>
            </dl>
          </div>
        </>
      )}

      {tab === 'bindings' && membership !== null && (
        <BindingDrawer
          workspaceId={membership.workspace_id}
          integrationId={integration.id}
          integrationKind={integration.kind}
          isAdmin={isAdmin}
          reloadKey={tabReloadKey}
        />
      )}

      {tab === 'events' && membership !== null && (
        <EventLedger
          workspaceId={membership.workspace_id}
          integrationId={integration.id}
          reloadKey={tabReloadKey}
        />
      )}

      {tab === 'queue' && integration.kind === 'im_dingtalk' && membership !== null && (
        <>
          <IntegrationQueuePanel
            workspaceId={membership.workspace_id}
            integrationId={integration.id}
            isAdmin={isAdmin}
            realtimeConnected={realtime?.state === 'connected'}
            refreshRequest={queueRefresh}
            onRefreshConsumed={consumeQueueRefresh}
          />
          <DingTalkInteractionGuide
            workspaceId={membership.workspace_id}
            workspaceSlug={membership.workspace_slug}
            verbosity={integration.config.verbosity === 'progress' ? 'progress' : 'final_only'}
            ackTemplate={String(integration.config.ack_template ?? DINGTALK_DEFAULT_ACK_TEMPLATE)}
          />
        </>
      )}

      <Dialog
        open={editOpen}
        onClose={() => setEditOpen(false)}
        title={t('integrations.actions.edit')}
        closeLabel={t('common.close')}
      >
        <Input
          label={t('integrations.add.nameLabel')}
          value={editName}
          onChange={(event) => setEditName(event.target.value)}
          data-testid="integration-edit-name"
        />
        {integration.kind === 'im_dingtalk' ? (
          <>
            <Input
              label={t('integrations.dingtalk.appKey')}
              value={editDingTalkAppKey}
              onChange={(event) => setEditDingTalkAppKey(event.target.value)}
              data-testid="integration-edit-dingtalk-app-key"
            />
            <Input
              label={t('integrations.dingtalk.corpId')}
              value={editDingTalkCorpId}
              onChange={(event) => setEditDingTalkCorpId(event.target.value)}
              data-testid="integration-edit-dingtalk-corp-id"
            />
            <Select
              label={t('integrations.dingtalk.receiveMode')}
              value={editDingTalkReceiveMode}
              onChange={(event) =>
                setEditDingTalkReceiveMode(event.target.value === 'http' ? 'http' : 'stream')
              }
              data-testid="integration-edit-dingtalk-receive-mode"
            >
              <option value="stream">{t('integrations.dingtalk.receive.stream')}</option>
              <option value="http">{t('integrations.dingtalk.receive.http')}</option>
            </Select>
            <Select
              label={t('integrations.dingtalk.verbosity')}
              value={editDingTalkVerbosity}
              onChange={(event) =>
                setEditDingTalkVerbosity(
                  event.target.value === 'progress' ? 'progress' : 'final_only',
                )
              }
              data-testid="integration-edit-dingtalk-verbosity"
            >
              <option value="final_only">{t('integrations.dingtalk.verbosity.final_only')}</option>
              <option value="progress">{t('integrations.dingtalk.verbosity.progress')}</option>
            </Select>
            <Input
              label={t('integrations.dingtalk.ackTemplate')}
              value={editDingTalkAckTemplate}
              onChange={(event) => setEditDingTalkAckTemplate(event.target.value)}
              data-testid="integration-edit-dingtalk-ack-template"
            />
          </>
        ) : (
          <div className="mesh-integrations__field">
            <label htmlFor="integration-edit-config">{t('integrations.detail.configTitle')}</label>
            <textarea
              id="integration-edit-config"
              rows={6}
              value={editConfig}
              onChange={(event) => setEditConfig(event.target.value)}
              data-testid="integration-edit-config"
            />
            <span className="mesh-integrations__muted">{t('integrations.add.configHint')}</span>
          </div>
        )}
        <div className="mesh-integrations__footer">
          <Button variant="ghost" onClick={() => setEditOpen(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            isLoading={busy}
            disabled={
              editName.trim() === '' ||
              (integration.kind === 'im_dingtalk' &&
                (editDingTalkAppKey.trim() === '' || editDingTalkCorpId.trim() === ''))
            }
            onClick={() => void submitEdit()}
            data-testid="integration-edit-submit"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={rotateOpen}
        onClose={() => setRotateOpen(false)}
        title={t('integrations.detail.rotate')}
        closeLabel={t('common.close')}
      >
        <p className="mesh-integrations__muted">{t('integrations.detail.rotateHint')}</p>
        <Input
          label={t('integrations.add.secretLabel')}
          type="password"
          value={rotateSecret}
          onChange={(event) => setRotateSecret(event.target.value)}
          hint={rotateSecret !== '' ? '••••' : undefined}
          data-testid="integration-rotate-secret"
        />
        <div className="mesh-integrations__footer">
          <Button variant="ghost" onClick={() => setRotateOpen(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            isLoading={busy}
            disabled={rotateSecret.trim() === ''}
            onClick={() => void submitRotate()}
            data-testid="integration-rotate-submit"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
