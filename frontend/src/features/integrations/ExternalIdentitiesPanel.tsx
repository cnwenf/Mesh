/**
 * 外部身份面板(integrations.md §4 / §3.1 建链·解链):列出**本人所属**的已连接
 * 外部身份(provider + external_user_key + verified_at);「连接外部账号」两步流程
 * (:link 下发一次性验证码 → :link-confirm 校验);解链二次确认(403
 * identity_unlink_forbidden —— 仅映射所属 users.id 本人可解,无 admin 旁路)。
 */
import { useCallback, useEffect, useState } from 'react';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import { Button, Dialog, EmptyState, Input, Select, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import {
  confirmExternalIdentity,
  linkExternalIdentity,
  listExternalIdentities,
  listIntegrations,
  unlinkExternalIdentity,
} from './api';
import type { ExternalIdentity, Integration } from './types';
import './integrations.css';

type LinkStep = 'form' | 'code';

function newClient(): MeshApiClient {
  return new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
}

/** 将捕获的错误归一为 i18n 键(同 WebhookConfigPage errorKeyOf)。 */
function errorKeyOf(error: unknown): string {
  return error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown';
}

export interface ExternalIdentitiesPanelProps {
  readonly workspaceId: string;
}

export function ExternalIdentitiesPanel(props: ExternalIdentitiesPanelProps): React.JSX.Element {
  const { workspaceId } = props;
  const t = useT();
  const toast = useToast();

  const [identities, setIdentities] = useState<ExternalIdentity[] | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [step, setStep] = useState<LinkStep>('form');
  const [provider, setProvider] = useState('feishu');
  const [integrationId, setIntegrationId] = useState('');
  const [externalUserKey, setExternalUserKey] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmUnlinkId, setConfirmUnlinkId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const client = newClient();
    void (async () => {
      try {
        const [identityListing, integrationListing] = await Promise.all([
          listExternalIdentities(client, workspaceId),
          listIntegrations(client, workspaceId, { limit: 50 }),
        ]);
        if (cancelled) return;
        setIdentities(identityListing.data);
        setIntegrations(integrationListing.data);
      } catch {
        if (!cancelled) setIdentities([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId, reloadKey]);

  const resetDialog = useCallback((): void => {
    setStep('form');
    setProvider('feishu');
    setIntegrationId('');
    setExternalUserKey('');
    setCode('');
  }, []);

  const openDialog = useCallback((): void => {
    resetDialog();
    setDialogOpen(true);
  }, [resetDialog]);

  const startLink = useCallback(async (): Promise<void> => {
    if (integrationId === '' || externalUserKey.trim() === '') return;
    setBusy(true);
    try {
      await linkExternalIdentity(newClient(), workspaceId, {
        provider,
        integration_id: integrationId,
        external_user_key: externalUserKey.trim(),
      });
      setStep('code');
    } catch (error) {
      toast.addToast(t(errorKeyOf(error)), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setBusy(false);
    }
  }, [workspaceId, provider, integrationId, externalUserKey, toast, t]);

  const confirmLink = useCallback(async (): Promise<void> => {
    if (code.trim() === '') return;
    setBusy(true);
    try {
      await confirmExternalIdentity(newClient(), workspaceId, {
        provider,
        integration_id: integrationId,
        code: code.trim(),
      });
      toast.addToast(t('integrations.identities.linkedToast'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setDialogOpen(false);
      setReloadKey((key) => key + 1);
    } catch (error) {
      toast.addToast(t(errorKeyOf(error)), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setBusy(false);
    }
  }, [workspaceId, provider, integrationId, code, toast, t]);

  const doUnlink = useCallback(
    async (identityId: string): Promise<void> => {
      setBusy(true);
      try {
        await unlinkExternalIdentity(newClient(), workspaceId, identityId);
        toast.addToast(t('integrations.identities.unlinkedToast'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
        setConfirmUnlinkId(null);
        setReloadKey((key) => key + 1);
      } catch (error) {
        toast.addToast(t(errorKeyOf(error)), {
          tone: 'danger',
          closeLabel: t('common.close'),
        });
      } finally {
        setBusy(false);
      }
    },
    [workspaceId, toast, t],
  );

  return (
    <div className="mesh-integrations__section" data-testid="external-identities-panel">
      <div className="mesh-integrations__header">
        <h3>{t('integrations.identities.title')}</h3>
        <Button variant="secondary" size="sm" onClick={openDialog} data-testid="identity-link-open">
          {t('integrations.identities.connect')}
        </Button>
      </div>
      <p className="mesh-integrations__muted">{t('integrations.identities.intro')}</p>

      {identities === null && <Skeleton loadingLabel={t('integrations.loading')} />}
      {identities !== null && identities.length === 0 && (
        <EmptyState title={t('integrations.identities.empty')} description="" />
      )}
      {identities !== null && identities.length > 0 && (
        <ul className="mesh-integrations__vcs-list" data-testid="identity-list">
          {identities.map((identity) => (
            <li key={identity.id} className="mesh-integrations__vcs-item" data-testid={`identity-row-${identity.id}`}>
              <span>
                <span className="mesh-integrations__tag">{identity.provider}</span>{' '}
                <span className="mesh-integrations__vcs-ref">{identity.external_user_key}</span>{' '}
                <span className="mesh-integrations__muted">
                  {new Date(identity.verified_at).toLocaleDateString()}
                </span>
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmUnlinkId(identity.id)}
                data-testid={`identity-unlink-${identity.id}`}
              >
                {t('integrations.identities.unlink')}
              </Button>
            </li>
          ))}
        </ul>
      )}

      <Dialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title={t('integrations.identities.connect')}
        closeLabel={t('common.close')}
      >
        {step === 'form' ? (
          <>
            <Select
              label={t('integrations.identities.providerLabel')}
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
              data-testid="identity-provider"
            >
              <option value="feishu">{t('integrations.provider.feishu')}</option>
              <option value="slack">{t('integrations.provider.slack')}</option>
              <option value="github">{t('integrations.provider.github')}</option>
              <option value="gitlab">{t('integrations.provider.gitlab')}</option>
            </Select>
            <Select
              label={t('integrations.identities.integrationLabel')}
              value={integrationId}
              onChange={(event) => setIntegrationId(event.target.value)}
              data-testid="identity-integration"
            >
              <option value="">{t('integrations.identities.integrationPlaceholder')}</option>
              {integrations.map((integration) => (
                <option key={integration.id} value={integration.id}>
                  {integration.name}
                </option>
              ))}
            </Select>
            <Input
              label={t('integrations.identities.externalKeyLabel')}
              value={externalUserKey}
              onChange={(event) => setExternalUserKey(event.target.value)}
              hint={t('integrations.identities.externalKeyHint')}
              data-testid="identity-external-key"
            />
            <div className="mesh-integrations__footer">
              <Button variant="ghost" onClick={() => setDialogOpen(false)}>
                {t('common.cancel')}
              </Button>
              <Button
                variant="primary"
                isLoading={busy}
                disabled={integrationId === '' || externalUserKey.trim() === ''}
                onClick={() => void startLink()}
                data-testid="identity-link-submit"
              >
                {t('integrations.identities.sendCode')}
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="mesh-integrations__muted" data-testid="identity-code-hint">
              {t('integrations.identities.codeHint')}
            </p>
            <Input
              label={t('integrations.identities.codeLabel')}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              data-testid="identity-code"
            />
            <div className="mesh-integrations__footer">
              <Button variant="ghost" onClick={() => setDialogOpen(false)}>
                {t('common.cancel')}
              </Button>
              <Button
                variant="primary"
                isLoading={busy}
                disabled={code.trim() === ''}
                onClick={() => void confirmLink()}
                data-testid="identity-confirm-submit"
              >
                {t('common.confirm')}
              </Button>
            </div>
          </>
        )}
      </Dialog>

      <Dialog
        open={confirmUnlinkId !== null}
        onClose={() => setConfirmUnlinkId(null)}
        title={t('integrations.identities.unlinkConfirmTitle')}
        closeLabel={t('common.close')}
      >
        <p>{t('integrations.identities.unlinkConfirmText')}</p>
        <div className="mesh-integrations__footer">
          <Button variant="ghost" onClick={() => setConfirmUnlinkId(null)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="danger"
            isLoading={busy}
            onClick={() => confirmUnlinkId !== null && void doUnlink(confirmUnlinkId)}
            data-testid="identity-unlink-confirm"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
