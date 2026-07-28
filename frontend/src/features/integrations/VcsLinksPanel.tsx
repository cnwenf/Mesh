/**
 * issue 侧栏 VCS 关联区块(integrations.md §4.2 / §3.3,真源 `vcs_links`):
 * 列出 active 关联(提供商图标 + external_object_ref + 状态徽章 + external_state
 * 快照,如 pr_state=merged),[+ 关联] 手动关联(选工作区 VCS 集成 + 对象类型 +
 * id 如 `owner/repo#123`),解除关联(DELETE → 204 软删后刷新)。
 */
import { useCallback, useEffect, useState } from 'react';
import { MeshApiClient, errorToI18nKey, getToken, MeshApiError } from '../../api';
import { Button, Dialog, EmptyState, Input, Select, Skeleton, StatusDot, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { createVcsLink, deleteVcsLink, listIntegrations, listIssueVcsLinks } from './api';
import { KIND_ICON, VCS_LINK_STATUS_TONE, formatExternalState } from './format';
import type { Integration, VcsLink, VcsObjectType } from './types';
import { VCS_OBJECT_TYPES } from './types';
import './integrations.css';

function newClient(): MeshApiClient {
  return new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
}

export interface VcsLinksPanelProps {
  readonly workspaceId: string;
  readonly issueId: string;
}

export function VcsLinksPanel(props: VcsLinksPanelProps): React.JSX.Element {
  const { workspaceId, issueId } = props;
  const t = useT();
  const toast = useToast();

  const [links, setLinks] = useState<VcsLink[] | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [integrationId, setIntegrationId] = useState('');
  const [objectType, setObjectType] = useState<VcsObjectType>('pull_request');
  const [objectId, setObjectId] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const client = newClient();
    void (async () => {
      try {
        const [linkListing, integrationListing] = await Promise.all([
          listIssueVcsLinks(client, issueId),
          listIntegrations(client, workspaceId, { limit: 50 }),
        ]);
        if (cancelled) return;
        setLinks(linkListing.data);
        setIntegrations(
          integrationListing.data.filter((integration) => integration.kind.startsWith('vcs_')),
        );
      } catch {
        if (!cancelled) setLinks([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId, issueId, reloadKey]);

  const submitLink = useCallback(async (): Promise<void> => {
    if (integrationId === '' || objectId.trim() === '') return;
    setBusy(true);
    try {
      await createVcsLink(newClient(), {
        integration_id: integrationId,
        vcs_ref: { type: objectType, id: objectId.trim() },
        mesh_entity_type: 'issue',
        issue_id: issueId,
      });
      toast.addToast(t('integrations.vcs.linkedToast'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setDialogOpen(false);
      setObjectId('');
      setReloadKey((key) => key + 1);
    } catch (error) {
      toast.addToast(t(error instanceof MeshApiError ? errorToI18nKey(error) : 'error.unknown'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    } finally {
      setBusy(false);
    }
  }, [integrationId, objectType, objectId, issueId, toast, t]);

  const removeLink = useCallback(
    async (linkId: string): Promise<void> => {
      try {
        await deleteVcsLink(newClient(), linkId);
        toast.addToast(t('integrations.vcs.unlinkedToast'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
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

  return (
    <div className="mesh-integrations__section" data-testid="vcs-links-panel">
      <div className="mesh-integrations__header">
        <h3>{t('integrations.vcs.title')}</h3>
        <Button variant="secondary" size="sm" onClick={() => setDialogOpen(true)} data-testid="vcs-link-open">
          {t('integrations.vcs.add')}
        </Button>
      </div>

      {links === null && <Skeleton loadingLabel={t('integrations.loading')} />}
      {links !== null && links.length === 0 && (
        <EmptyState title={t('integrations.vcs.empty')} description="" />
      )}
      {links !== null && links.length > 0 && (
        <ul className="mesh-integrations__vcs-list" data-testid="vcs-link-list">
          {links.map((link) => {
            const stateLabel = formatExternalState(link.external_state);
            return (
              <li key={link.id} className="mesh-integrations__vcs-item" data-testid={`vcs-link-row-${link.id}`}>
                <span>
                  <span aria-hidden="true">{KIND_ICON[link.provider === 'github' ? 'vcs_github' : 'vcs_gitlab']}</span>{' '}
                  <span className="mesh-integrations__vcs-ref">{link.external_object_ref}</span>{' '}
                  <StatusDot
                    tone={VCS_LINK_STATUS_TONE[link.status]}
                    label={t(`integrations.vcs.status.${link.status}`)}
                  />
                  {stateLabel !== null ? <span className="mesh-integrations__muted"> {stateLabel}</span> : null}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void removeLink(link.id)}
                  data-testid={`vcs-unlink-${link.id}`}
                >
                  {t('integrations.vcs.unlink')}
                </Button>
              </li>
            );
          })}
        </ul>
      )}

      <Dialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title={t('integrations.vcs.add')}
        closeLabel={t('common.close')}
      >
        <Select
          label={t('integrations.vcs.integrationLabel')}
          value={integrationId}
          onChange={(event) => setIntegrationId(event.target.value)}
          data-testid="vcs-integration"
        >
          <option value="">{t('integrations.vcs.integrationPlaceholder')}</option>
          {integrations.map((integration) => (
            <option key={integration.id} value={integration.id}>
              {integration.name}
            </option>
          ))}
        </Select>
        <Select
          label={t('integrations.vcs.typeLabel')}
          value={objectType}
          onChange={(event) => setObjectType(event.target.value as VcsObjectType)}
          data-testid="vcs-type"
        >
          {VCS_OBJECT_TYPES.map((type) => (
            <option key={type} value={type}>
              {t(`integrations.vcs.type.${type}`)}
            </option>
          ))}
        </Select>
        <Input
          label={t('integrations.vcs.idLabel')}
          value={objectId}
          onChange={(event) => setObjectId(event.target.value)}
          hint={t('integrations.vcs.idHint')}
          data-testid="vcs-object-id"
        />
        <div className="mesh-integrations__footer">
          <Button variant="ghost" onClick={() => setDialogOpen(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            isLoading={busy}
            disabled={integrationId === '' || objectId.trim() === ''}
            onClick={() => void submitLink()}
            data-testid="vcs-link-submit"
          >
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
