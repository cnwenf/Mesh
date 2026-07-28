/**
 * 导出对话框(import-export.md §4.3):范围/格式选择 → 建异步作业 → 进度 → 下载。
 * 进度经 data_job:{id} 实时帧;完成后签名 URL 下载。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { getApiClient } from '../../api';
import type { MeshApiClient } from '../../api';
import { Button } from '../../design/components/Button';
import { Dialog } from '../../design/components/Dialog';
import { useToast } from '../../design/components/Toast';
import { useT } from '../../i18n';
import { errorToI18nKey } from '../../api/errors';
import { MeshApiError } from '../../api';
import { useRealtimeContext } from '../../shell/AppShell';
import { createExportJob, downloadDataJobProduct, getDataJob } from './api';
import { applyDataJobFrame } from './realtime';
import type { DataJob, DataJobFormat } from './types';
import { dataJobChannel, isTerminalDataJobStatus } from './types';
import './dataJobs.css';

export type ExportScope = 'project' | 'workspace' | 'view';

export interface ExportDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly workspaceId: string;
  /** 情境入口:项目页传 project,视图页传 view + filters。 */
  readonly defaultScope?: ExportScope;
  readonly projectId?: string | null;
  readonly filters?: Record<string, unknown>;
  readonly client?: MeshApiClient;
}

export function ExportDialog(props: ExportDialogProps): React.JSX.Element | null {
  const { open, onClose, workspaceId, projectId, filters } = props;
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => props.client ?? getApiClient(), [props.client]);
  const realtime = useRealtimeContext();

  const [scope, setScope] = useState<ExportScope>(props.defaultScope ?? 'workspace');
  const [format, setFormat] = useState<DataJobFormat>('csv');
  const [job, setJob] = useState<DataJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);

  const reset = useCallback(() => {
    setJob(null);
    setBusy(false);
    setDownloadUrl(null);
    setScope(props.defaultScope ?? 'workspace');
    setFormat('csv');
  }, [props.defaultScope]);

  const handleClose = useCallback(() => {
    reset();
    onClose();
  }, [onClose, reset]);

  useEffect(() => {
    if (job === null || realtime === null) return;
    const channel = dataJobChannel(job.id);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      setJob((current) => {
        if (current === null) return current;
        const merged = applyDataJobFrame([current], frame);
        return merged[0] ?? current;
      });
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [job, realtime]);

  /** 终态:completed → 取签名下载 URL;failed → toast(§4.3-4)。 */
  useEffect(() => {
    if (job === null) return;
    if (job.status === 'completed') {
      let cancelled = false;
      void downloadDataJobProduct(client, job.id)
        .then((descriptor) => {
          if (!cancelled) setDownloadUrl(descriptor.url);
        })
        .catch((error: unknown) => {
          const key = error instanceof MeshApiError ? errorToI18nKey(error) : 'common.unknownError';
          toast.addToast(t(key), {
            tone: 'danger',
            closeLabel: t('common.close'),
          });
        });
      return () => {
        cancelled = true;
      };
    }
    if (job.status === 'failed') {
      toast.addToast(t('dataJobs.export.failedToast'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
    return undefined;
  }, [job, client, t, toast]);

  const submit = useCallback(async () => {
    setBusy(true);
    try {
      const created = await createExportJob(
        client,
        {
          workspace_id: workspaceId,
          entity_type: 'issues',
          format,
          scope,
          ...(scope === 'project' && projectId != null ? { project_id: projectId } : {}),
          ...(filters !== undefined ? { filters } : {}),
        },
        crypto.randomUUID(),
      );
      setJob(created);
      // 异步作业:短轮询兜底(无实时通道时也能收敛)。
      if (realtime === null) {
        let current = created;
        for (let i = 0; i < 120 && !isTerminalDataJobStatus(current.status); i += 1) {
          await new Promise((resolve) => setTimeout(resolve, 1000));
          current = await getDataJob(client, created.id);
          setJob(current);
        }
      }
    } catch (error) {
      const key = error instanceof MeshApiError ? errorToI18nKey(error) : 'common.unknownError';
          toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    } finally {
      setBusy(false);
    }
  }, [client, workspaceId, format, scope, projectId, filters, realtime, t, toast]);

  const submitted = job !== null;

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      title={t('dataJobs.export.title')}
      closeLabel={t('common.close')}
    >
      {!submitted && (
        <section aria-label={t('dataJobs.export.configure')}>
          <label>
            {t('dataJobs.export.scopeLabel')}
            <select
              value={scope}
              data-testid="export-scope-select"
              onChange={(event) => setScope(event.target.value as ExportScope)}
            >
              <option value="workspace">{t('dataJobs.export.scope.workspace')}</option>
              <option value="project">{t('dataJobs.export.scope.project')}</option>
              <option value="view">{t('dataJobs.export.scope.view')}</option>
            </select>
          </label>
          <label>
            {t('dataJobs.export.formatLabel')}
            <select
              value={format}
              data-testid="export-format-select"
              onChange={(event) => setFormat(event.target.value as DataJobFormat)}
            >
              <option value="csv">CSV</option>
              <option value="json">JSON</option>
            </select>
          </label>
          <div className="mesh-data-jobs__actions">
            <Button
              variant="primary"
              onClick={() => void submit()}
              isLoading={busy}
              disabled={scope === 'project' && projectId == null}
              data-testid="export-submit-button"
            >
              {t('dataJobs.export.submit')}
            </Button>
          </div>
        </section>
      )}

      {submitted && job !== null && (
        <section aria-label={t('dataJobs.export.progress')}>
          <p data-testid="export-status">{t(`dataJobs.status.${job.status}`)}</p>
          {!isTerminalDataJobStatus(job.status) && (
            <p>{t('dataJobs.export.runningHint')}</p>
          )}
          {job.status === 'failed' && job.failure_reason !== null && (
            <p>{t('dataJobs.import.failureReason', { reason: job.failure_reason })}</p>
          )}
          {downloadUrl !== null && (
            <a href={downloadUrl} data-testid="export-download-link">
              {t('dataJobs.export.download')}
            </a>
          )}
          <div className="mesh-data-jobs__actions">
            <Button variant="secondary" onClick={handleClose}>
              {t('common.close')}
            </Button>
          </div>
        </section>
      )}
    </Dialog>
  );
}
