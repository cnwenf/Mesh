/**
 * 集成管理页(integrations.md §4.1):连接器目录卡片网格(5 卡 + 能力标签)+
 * 已连接集成表(名称 / 类型图标 / 状态徽章 / 绑定数 / 操作)+「添加集成」Dialog
 * (kind + name + 非密 config JSON + 掩码 secret)+ 每 kind OAuth 连接(整页跳授权)。
 * RBAC 呈现级:非 admin 只读(隐藏写按钮 + Banner,后端权威校验)。行级实时:
 * workspace:{ws}:integrations 频道的 integration.updated / integration.event_ingested
 * 帧触发整列重拉(§3.6)。OAuth 回跳 `?oauth=success|error` → Banner。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import {
  Banner,
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Icon,
  IconButton,
  Input,
  Select,
  Skeleton,
  StatusDot,
  useToast,
} from '../../design';
import type { IconName } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { canViewSettings } from '../../workspace/permissions';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import {
  createIntegration,
  deleteIntegration,
  integrationAuthorizeUrl,
  listBindings,
  listIntegrations,
  patchIntegration,
  testIntegration,
  workspaceIntegrationsChannel,
} from './api';
import { ExternalIdentitiesPanel } from './ExternalIdentitiesPanel';
import { HEALTH_STATE_TONE, INTEGRATION_STATUS_TONE, KIND_ICON, toHealthState } from './format';
import type { Integration, IntegrationKind } from './types';
import { CONNECTOR_CATALOG, INTEGRATION_KINDS, OAUTH_KINDS } from './types';
import './integrations.css';

const PAGE_LIMIT = 100;

/** 各 kind 的非密 config JSON 占位提示(§2.7;密钥走 secret 字段,不入 config)。 */
const CONFIG_HINTS: Record<IntegrationKind, string> = {
  im_feishu: '{ "app_id": "cli_xxx", "callback_base": "https://mesh.example.com/api/v1/integrations/feishu" }',
  im_slack: '{ "app_id": "A0xxx", "team_id": "T0xxx", "bot_user_id": "U0xxx" }',
  vcs_github: '{ "installation_id": "1234567", "api_base": "https://api.github.com" }',
  vcs_gitlab: '{ "instance_url": "https://gitlab.com" }',
  webhook_outbound: '{}',
};

const LIST_EVENTS: ReadonlySet<string> = new Set([
  'integration.updated',
  'integration.event_ingested',
]);

function newClient(): MeshApiClient {
  return new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
}

interface ConnectorCardProps {
  readonly kind: IntegrationKind;
  readonly icon: IconName;
  readonly nameKey: string;
  readonly capabilityKeys: ReadonlyArray<string>;
  readonly connectedCount: number;
  readonly isAdmin: boolean;
  readonly onConnect: (kind: IntegrationKind) => void;
}

function ConnectorCard(props: ConnectorCardProps): React.JSX.Element {
  const { kind, icon, nameKey, capabilityKeys, connectedCount, isAdmin, onConnect } = props;
  const t = useT();
  return (
    <div className="mesh-integrations__card" data-testid={`connector-card-${kind}`}>
      <span className="mesh-integrations__card-icon" aria-hidden="true">
        <Icon name={icon} size={24} />
      </span>
      <h4 className="mesh-integrations__card-name">{t(nameKey)}</h4>
      <div className="mesh-integrations__card-caps">
        {capabilityKeys.map((key) => (
          <span key={key} className="mesh-integrations__tag">
            {t(key)}
          </span>
        ))}
      </div>
      <div className="mesh-integrations__card-footer">
        {connectedCount > 0 ? (
          <span className="mesh-integrations__muted" data-testid={`connector-count-${kind}`}>
            {t('integrations.catalog.connected', { count: connectedCount })}
          </span>
        ) : (
          <span className="mesh-integrations__muted">{t('integrations.catalog.notConnected')}</span>
        )}
        {isAdmin && (
          <Button variant="secondary" size="sm" onClick={() => onConnect(kind)} data-testid={`connector-connect-${kind}`}>
            {t('integrations.catalog.connect')}
          </Button>
        )}
      </div>
    </div>
  );
}

interface AddIntegrationDialogProps {
  readonly open: boolean;
  readonly isAdmin: boolean;
  readonly initialKind: IntegrationKind;
  readonly busy: boolean;
  readonly onClose: () => void;
  readonly onSubmit: (input: { kind: IntegrationKind; name: string; config: string; secret: string }) => void;
  readonly onOAuth: (kind: IntegrationKind) => void;
}

function AddIntegrationDialog(props: AddIntegrationDialogProps): React.JSX.Element {
  const { open, initialKind, busy, onClose, onSubmit, onOAuth } = props;
  const t = useT();
  const [kind, setKind] = useState<IntegrationKind>(initialKind);
  const [name, setName] = useState('');
  const [config, setConfig] = useState('');
  const [secret, setSecret] = useState('');

  useEffect(() => {
    if (open) {
      setKind(initialKind);
      setName('');
      setConfig('');
      setSecret('');
    }
  }, [open, initialKind]);

  const supportsOAuth = OAUTH_KINDS.has(kind);

  return (
    <Dialog open={open} onClose={onClose} title={t('integrations.add.title')} closeLabel={t('common.close')}>
      <Select
        label={t('integrations.add.kindLabel')}
        value={kind}
        onChange={(event) => setKind(event.target.value as IntegrationKind)}
        data-testid="integration-add-kind"
      >
        {INTEGRATION_KINDS.map((candidate) => (
          <option key={candidate} value={candidate}>
            {t(`integrations.kind.${candidate}`)}
          </option>
        ))}
      </Select>
      <Input
        label={t('integrations.add.nameLabel')}
        value={name}
        onChange={(event) => setName(event.target.value)}
        data-testid="integration-add-name"
      />
      <div className="mesh-integrations__field">
        <label htmlFor="integration-add-config">{t('integrations.add.configLabel')}</label>
        <textarea
          id="integration-add-config"
          rows={4}
          value={config}
          placeholder={CONFIG_HINTS[kind]}
          onChange={(event) => setConfig(event.target.value)}
          data-testid="integration-add-config"
        />
        <span className="mesh-integrations__muted">{t('integrations.add.configHint')}</span>
      </div>
      <Input
        label={t('integrations.add.secretLabel')}
        type="password"
        value={secret}
        onChange={(event) => setSecret(event.target.value)}
        hint={secret !== '' ? '••••' : t('integrations.add.secretHint')}
        data-testid="integration-add-secret"
      />
      {supportsOAuth && (
        <Button
          variant="secondary"
          onClick={() => onOAuth(kind)}
          data-testid="integration-add-oauth"
        >
          {t('integrations.add.oauth', { name: t(`integrations.kind.${kind}`) })}
        </Button>
      )}
      <div className="mesh-integrations__footer">
        <Button variant="ghost" onClick={onClose}>
          {t('common.cancel')}
        </Button>
        <Button
          variant="primary"
          isLoading={busy}
          disabled={name.trim() === ''}
          onClick={() => onSubmit({ kind, name: name.trim(), config, secret })}
          data-testid="integration-add-submit"
        >
          {t('common.confirm')}
        </Button>
      </div>
    </Dialog>
  );
}

export function IntegrationsPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const realtime = useRealtimeContext();
  const [searchParams, setSearchParams] = useSearchParams();

  const [membership, setMembership] = useState<Membership | null>(null);
  const [integrations, setIntegrations] = useState<Integration[] | null>(null);
  const [bindingCounts, setBindingCounts] = useState<Record<string, number>>({});
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [addOpen, setAddOpen] = useState(false);
  const [addKind, setAddKind] = useState<IntegrationKind>('im_feishu');
  const [busy, setBusy] = useState(false);
  const [disableTarget, setDisableTarget] = useState<Integration | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Integration | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);

  const oauthResult = searchParams.get('oauth');
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
          setIntegrations([]);
          return;
        }
        setMembership(workspace);
        const listing = await listIntegrations(client, workspace.workspace_id, { limit: PAGE_LIMIT });
        const counts = await Promise.all(
          listing.data.map(async (integration) => {
            const bindings = await listBindings(client, workspace.workspace_id, integration.id);
            return [integration.id, bindings.data.length] as const;
          }),
        );
        if (cancelled) return;
        setIntegrations(listing.data);
        setBindingCounts(Object.fromEntries(counts));
        setErrorKey(null);
      } catch (error) {
        if (cancelled) return;
        setErrorKey(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown');
        setIntegrations(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  useEffect(() => {
    if (realtime === null || membership === null) return;
    const channel = workspaceIntegrationsChannel(membership.workspace_id);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      if (LIST_EVENTS.has(frame.event)) setReloadKey((key) => key + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, membership]);

  const dismissOAuth = useCallback((): void => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('oauth');
        return next;
      },
      { replace: true },
    );
  }, [setSearchParams]);

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

  const openAdd = useCallback((kind: IntegrationKind): void => {
    setAddKind(kind);
    setAddOpen(true);
  }, []);

  const startOAuth = useCallback(
    (kind: IntegrationKind): void => {
      if (membership === null) return;
      window.location.assign(integrationAuthorizeUrl(membership.workspace_id, kind));
    },
    [membership],
  );

  const handleConnect = useCallback(
    (kind: IntegrationKind): void => {
      if (OAUTH_KINDS.has(kind)) startOAuth(kind);
      else openAdd(kind);
    },
    [startOAuth, openAdd],
  );

  const submitAdd = useCallback(
    (input: { kind: IntegrationKind; name: string; config: string; secret: string }): void => {
      if (membership === null) return;
      let configObject: Record<string, unknown> | undefined;
      if (input.config.trim() !== '') {
        try {
          configObject = JSON.parse(input.config) as Record<string, unknown>;
        } catch {
          toast.addToast(t('integrations.add.invalidConfig'), {
            tone: 'danger',
            closeLabel: t('common.close'),
          });
          return;
        }
      }
      setBusy(true);
      void runAction(
        () =>
          createIntegration(newClient(), membership.workspace_id, {
            kind: input.kind,
            name: input.name,
            config: configObject,
            secret: input.secret !== '' ? input.secret : undefined,
          }),
        t('integrations.add.createdToast'),
      ).finally(() => {
        setBusy(false);
        setAddOpen(false);
      });
    },
    [membership, runAction, toast, t],
  );

  const toggleStatus = useCallback(
    (integration: Integration): void => {
      if (membership === null) return;
      const nextStatus = integration.status === 'active' ? 'disabled' : 'active';
      void runAction(
        () => patchIntegration(newClient(), membership.workspace_id, integration.id, { status: nextStatus }),
        nextStatus === 'active' ? t('integrations.toast.enabled') : t('integrations.toast.disabled'),
      );
      setDisableTarget(null);
    },
    [membership, runAction, t],
  );

  const confirmDelete = useCallback(
    (integration: Integration): void => {
      if (membership === null) return;
      void runAction(
        () => deleteIntegration(newClient(), membership.workspace_id, integration.id),
        t('integrations.toast.deleted'),
      );
      setDeleteTarget(null);
    },
    [membership, runAction, t],
  );

  const testConnection = useCallback(
    (integration: Integration): void => {
      if (membership === null) return;
      setTestingId(integration.id);
      void (async () => {
        try {
          const result = await testIntegration(newClient(), membership.workspace_id, integration.id);
          const nextState = toHealthState(result.health_state);
          setIntegrations((prev) =>
            prev === null
              ? prev
              : prev.map((item) =>
                  item.id === integration.id
                    ? { ...item, health_state: nextState, last_error: result.detail }
                    : item,
                ),
          );
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
          setTestingId(null);
        }
      })();
    },
    [membership, toast, t],
  );

  const connectedByKind = useMemo(() => {
    const counts: Partial<Record<IntegrationKind, number>> = {};
    for (const integration of integrations ?? []) {
      counts[integration.kind] = (counts[integration.kind] ?? 0) + 1;
    }
    return counts;
  }, [integrations]);

  if (membership === null && integrations !== null && integrations.length === 0 && errorKey === null) {
    return (
      <div className="mesh-integrations__page">
        <EmptyState title={t('integrations.noWorkspace.title')} description={t('integrations.noWorkspace.description')} />
      </div>
    );
  }

  return (
    <div className="mesh-integrations__page" data-testid="integrations-page">
      <div className="mesh-integrations__header">
        <h1 className="mesh-integrations__title">{t('integrations.title')}</h1>
        {isAdmin && (
          <Button variant="primary" onClick={() => openAdd('im_feishu')} data-testid="integration-create">
            {t('integrations.add.trigger')}
          </Button>
        )}
      </div>

      {oauthResult === 'success' && (
        <div data-testid="oauth-success-banner">
          <Banner tone="success" onDismiss={dismissOAuth} dismissLabel={t('common.close')}>
            {t('integrations.oauth.success')}
          </Banner>
        </div>
      )}
      {oauthResult === 'error' && (
        <div data-testid="oauth-error-banner">
          <Banner tone="danger" onDismiss={dismissOAuth} dismissLabel={t('common.close')}>
            {t('integrations.oauth.error')}
          </Banner>
        </div>
      )}
      {!isAdmin && membership !== null && (
        <div data-testid="integrations-readonly-banner">
          <Banner tone="info">{t('integrations.readonly')}</Banner>
        </div>
      )}

      {/* 连接器目录卡片网格(§4.2)。 */}
      <div className="mesh-integrations__catalog" data-testid="integrations-catalog">
        {CONNECTOR_CATALOG.map((meta) => (
          <ConnectorCard
            key={meta.kind}
            kind={meta.kind}
            icon={meta.icon}
            nameKey={meta.nameKey}
            capabilityKeys={meta.capabilityKeys}
            connectedCount={connectedByKind[meta.kind] ?? 0}
            isAdmin={isAdmin}
            onConnect={handleConnect}
          />
        ))}
      </div>

      {errorKey !== null && (
        <ErrorState
          title={t(errorKey)}
          retryLabel={t('common.retry')}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      )}
      {integrations === null && errorKey === null && <Skeleton loadingLabel={t('integrations.loading')} />}
      {integrations !== null && integrations.length === 0 && errorKey === null && (
        <EmptyState title={t('integrations.empty.title')} description={t('integrations.empty.description')} />
      )}
      {integrations !== null && integrations.length > 0 && (
        <table className="mesh-integrations__table" data-testid="integrations-table">
          <thead>
            <tr>
              <th>{t('integrations.columns.name')}</th>
              <th>{t('integrations.columns.kind')}</th>
              <th>{t('integrations.columns.status')}</th>
              <th>{t('integrations.columns.bindings')}</th>
              <th>{t('integrations.columns.events7d')}</th>
              <th>{t('integrations.columns.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {integrations.map((integration) => (
              <tr
                key={integration.id}
                className="mesh-integrations__row"
                data-testid={`integration-row-${integration.id}`}
                onClick={() => navigate(`/integrations/${integration.id}`)}
              >
                <td className="mesh-integrations__cell-name" data-testid={`integration-name-${integration.id}`}>
                  {integration.name}
                </td>
                <td>
                  <Icon name={KIND_ICON[integration.kind]} size={16} />{' '}
                  {t(`integrations.kind.${integration.kind}`)}
                </td>
                <td>
                  <StatusDot
                    tone={INTEGRATION_STATUS_TONE[integration.status]}
                    label={t(`integrations.status.${integration.status}`)}
                  />
                  <span
                    data-testid={`integration-health-${integration.id}`}
                    title={integration.last_error ?? undefined}
                  >
                    <StatusDot
                      tone={HEALTH_STATE_TONE[integration.health_state]}
                      label={t(`integrations.health.${integration.health_state}`)}
                    />
                  </span>
                </td>
                <td data-testid={`integration-bindings-${integration.id}`}>
                  {bindingCounts[integration.id] ?? 0}
                </td>
                <td data-testid={`integration-events7d-${integration.id}`}>{integration.events_7d}</td>
                <td className="mesh-integrations__actions" onClick={(event) => event.stopPropagation()}>
                  <IconButton
                    label={t('integrations.actions.detail')}
                    size="sm"
                    onClick={() => navigate(`/integrations/${integration.id}`)}
                    data-testid={`integration-detail-${integration.id}`}
                  >
                    <Icon name="settings" size={16} />
                  </IconButton>
                  {isAdmin &&
                    integration.health_state === 'auth_failed' &&
                    OAUTH_KINDS.has(integration.kind) && (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => startOAuth(integration.kind)}
                        data-testid={`integration-reauth-${integration.id}`}
                      >
                        {t('integrations.actions.reauthorize')}
                      </Button>
                    )}
                  {isAdmin && (
                    <Button
                      variant="secondary"
                      size="sm"
                      isLoading={testingId === integration.id}
                      onClick={() => testConnection(integration)}
                      data-testid={`integration-test-${integration.id}`}
                    >
                      {t('integrations.actions.test')}
                    </Button>
                  )}
                  {isAdmin && (
                    <IconButton
                      label={
                        integration.status === 'active'
                          ? t('integrations.actions.disable')
                          : t('integrations.actions.enable')
                      }
                      size="sm"
                      onClick={() => setDisableTarget(integration)}
                      data-testid={`integration-toggle-${integration.id}`}
                    >
                      {integration.status === 'active' ? (
                        <Icon name="pause" size={16} />
                      ) : (
                        <Icon name="play" size={16} />
                      )}
                    </IconButton>
                  )}
                  {isAdmin && (
                    <IconButton
                      label={t('integrations.actions.delete')}
                      size="sm"
                      onClick={() => setDeleteTarget(integration)}
                      data-testid={`integration-delete-${integration.id}`}
                    >
                      ⋯
                    </IconButton>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {membership !== null && <ExternalIdentitiesPanel workspaceId={membership.workspace_id} />}

      <AddIntegrationDialog
        open={addOpen}
        isAdmin={isAdmin}
        initialKind={addKind}
        busy={busy}
        onClose={() => setAddOpen(false)}
        onSubmit={submitAdd}
        onOAuth={startOAuth}
      />

      <Dialog
        open={disableTarget !== null}
        onClose={() => setDisableTarget(null)}
        title={t('integrations.toggle.dialogTitle')}
        closeLabel={t('common.close')}
      >
        <p data-testid="integration-toggle-text">
          {disableTarget !== null && disableTarget.status === 'active'
            ? t('integrations.toggle.disableConfirm')
            : t('integrations.toggle.enableConfirm')}
        </p>
        <div className="mesh-integrations__footer">
          <Button variant="ghost" onClick={() => setDisableTarget(null)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            onClick={() => disableTarget !== null && toggleStatus(disableTarget)}
            data-testid="integration-toggle-confirm"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title={t('integrations.delete.dialogTitle')}
        closeLabel={t('common.close')}
      >
        <p>{t('integrations.delete.confirmText')}</p>
        <div className="mesh-integrations__footer">
          <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="danger"
            onClick={() => deleteTarget !== null && confirmDelete(deleteTarget)}
            data-testid="integration-delete-confirm"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
