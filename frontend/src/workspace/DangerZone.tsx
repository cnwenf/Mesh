/**
 * 危险操作区(workspace.md §4.2:删除需输入工作区 slug 二次确认,仅 owner 可见可操作)。
 *
 * 后端 DELETE body 携带 confirm_slug;输错 → 400 validation_error;非 owner → 403。
 * 删除成功经 realtime workspace.deleted 全员收到;本端删除后返回首页。
 */
import { useState } from 'react';
import { useNavigate } from 'react-router';
import type { MeshApiClient } from '../api/client';
import { MeshApiError, errorToI18nKey } from '../api/errors';
import { getApiClient } from '../api/instance';
import { deleteWorkspace } from '../api/workspace';
import { Button, Dialog, Input, useToast } from '../design';
import { useT } from '../i18n';

export interface DangerZoneProps {
  workspaceId: string;
  workspaceSlug: string;
  client?: MeshApiClient;
}

export function DangerZone(props: DangerZoneProps): React.JSX.Element {
  const { workspaceId, workspaceSlug } = props;
  const client = props.client ?? getApiClient();
  const t = useT();
  const navigate = useNavigate();
  const { addToast } = useToast();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmSlug, setConfirmSlug] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  const confirmMatches = confirmSlug === workspaceSlug;

  const handleDelete = async (): Promise<void> => {
    setIsSubmitting(true);
    setErrorKey(null);
    try {
      await deleteWorkspace(client, workspaceId, confirmSlug);
      addToast(t('danger.deletedToast'), { tone: 'warn', closeLabel: t('a11y.dismiss') });
      navigate('/');
    } catch (err) {
      setIsSubmitting(false);
      setErrorKey(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown');
    }
  };

  return (
    <div className="mesh-danger" data-testid="danger-zone">
      <h3>{t('danger.title')}</h3>
      <p>{t('danger.description')}</p>
      <Button
        variant="danger"
        data-testid="danger-open"
        onClick={() => {
          setConfirmSlug('');
          setErrorKey(null);
          setDialogOpen(true);
        }}
      >
        {t('danger.deleteButton')}
      </Button>
      <Dialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title={t('danger.dialogTitle')}
        closeLabel={t('a11y.closeDialog')}
      >
        <p>{t('danger.dialogHint', { slug: workspaceSlug })}</p>
        <Input
          label={t('danger.confirmLabel')}
          value={confirmSlug}
          data-testid="danger-confirm-input"
          onChange={(event) => setConfirmSlug(event.target.value)}
        />
        {errorKey !== null ? (
          <p role="alert" data-testid="danger-error">
            {t(errorKey)}
          </p>
        ) : null}
        <Button
          variant="danger"
          data-testid="danger-confirm"
          disabled={!confirmMatches}
          isLoading={isSubmitting}
          onClick={() => void handleDelete()}
        >
          {t('danger.confirmDelete')}
        </Button>
      </Dialog>
    </div>
  );
}
