/**
 * 数据管理页(import-export.md §4.1):设置 → 数据管理(管理员区)。
 * 作业列表(导入/导出历史、状态、计数、重新下载)+ 导入/导出主入口。
 * 行级实时进度(§4.4):订阅各在途作业的 data_job:{id} 频道合并 data_job.updated;
 * 无实时通道时轮询兜底,进度可收敛。异常态矩阵(§6.12):错误态可重试 / 骨架 / 空态。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router';

import { getApiClient } from '../../api';
import { Button } from '../../design/components/Button';
import { EmptyState } from '../../design/components/EmptyState';
import { ErrorState } from '../../design/components/ErrorState';
import { Skeleton } from '../../design/components/Skeleton';
import { useToast } from '../../design/components/Toast';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { useWorkspace } from '../../workspace/WorkspaceProvider';
import { listDataJobs } from './api';
import { ExportDialog } from './ExportDialog';
import { ImportWizard } from './ImportWizard';
import { applyDataJobFrame } from './realtime';
import { dataJobChannel, isTerminalDataJobStatus } from './types';
import type { DataJob } from './types';
import './dataJobs.css';

/** 无实时通道时的进度轮询间隔(§4.3-4 收敛兜底)。 */
const POLL_INTERVAL_MS = 5000;

export function DataManagementPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const { workspaceSlug } = useParams();
  const { workspace, isAdmin } = useWorkspace();
  const client = getApiClient();
  const realtime = useRealtimeContext();

  const [jobs, setJobs] = useState<readonly DataJob[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);

  const load = useCallback(async () => {
    if (workspace === null) return;
    setLoadError(false);
    try {
      const page = await listDataJobs(client, { workspace_id: workspace.id, limit: 100 });
      setJobs(page.data);
    } catch {
      setLoadError(true);
    }
  }, [client, workspace]);

  useEffect(() => {
    void load();
  }, [load]);

  // 在途作业(非终态)的 data_job:{id} 频道集;以连接串作 effect 依赖,
  // 避免每次帧合并产生的新数组引发重复订阅。
  const activeChannelKey = useMemo(() => {
    if (jobs === null) return '';
    return jobs
      .filter((job) => !isTerminalDataJobStatus(job.status))
      .map((job) => dataJobChannel(job.id))
      .join('|');
  }, [jobs]);
  const activeChannelsRef = useRef<readonly string[]>([]);
  activeChannelsRef.current = activeChannelKey === '' ? [] : activeChannelKey.split('|');

  useEffect(() => {
    if (activeChannelKey === '') return;
    if (realtime !== null) {
      const channels = activeChannelsRef.current;
      channels.forEach((channel) => realtime.client.subscribe(channel));
      const off = realtime.client.onFrame((frame) => {
        setJobs((current) => (current === null ? current : applyDataJobFrame(current, frame)));
      });
      return () => {
        off();
        channels.forEach((channel) => realtime.client.unsubscribe(channel));
      };
    }
    // 无实时通道:轮询兜底,保证行级进度最终收敛(§4.3-4)。
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [realtime, activeChannelKey, load]);

  const handleDownload = useCallback(
    (job: DataJob) => {
      if (job.result_attachment_id === null) return;
      window.open(`/api/v1/data-jobs/${job.id}/download`, '_blank', 'noopener');
      toast.addToast(t('dataJobs.page.downloadStarted'), {
        tone: 'info',
        closeLabel: t('common.close'),
      });
    },
    [t, toast],
  );

  return (
    <div className="mesh-settings__page" aria-label={t('dataJobs.page.title')}>
      <h1>{t('dataJobs.page.title')}</h1>
      <p>{t('dataJobs.page.subtitle', { workspace: workspaceSlug ?? '' })}</p>

      {isAdmin && (
        <div className="mesh-data-jobs__actions">
          <Button
            variant="primary"
            onClick={() => setImportOpen(true)}
            data-testid="open-import-wizard"
          >
            {t('dataJobs.page.importButton')}
          </Button>
          <Button
            variant="secondary"
            onClick={() => setExportOpen(true)}
            data-testid="open-export-dialog"
          >
            {t('dataJobs.page.exportButton')}
          </Button>
        </div>
      )}

      {loadError && (
        <ErrorState
          title={t('dataJobs.page.loadError')}
          onRetry={() => void load()}
          retryLabel={t('common.retry')}
        />
      )}

      {!loadError && jobs === null && <Skeleton loadingLabel={t('dataJobs.page.loading')} />}

      {!loadError && jobs !== null && jobs.length === 0 && (
        <EmptyState
          title={t('dataJobs.page.emptyTitle')}
          description={t('dataJobs.page.emptyDescription')}
        />
      )}

      {!loadError && jobs !== null && jobs.length > 0 && (
        <table
          className="mesh-data-jobs__table"
          aria-label={t('dataJobs.page.tableLabel')}
          tabIndex={0}
        >
          <caption className="sr-only">{t('dataJobs.page.tableLabel')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('dataJobs.page.col.kind')}</th>
              <th scope="col">{t('dataJobs.page.col.entity')}</th>
              <th scope="col">{t('dataJobs.page.col.status')}</th>
              <th scope="col">{t('dataJobs.page.col.rows')}</th>
              <th scope="col">{t('dataJobs.page.col.created')}</th>
              <th scope="col">{t('dataJobs.page.col.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id} data-testid={`job-row-${job.id}`}>
                <td>{t(`dataJobs.kind.${job.kind}`)}</td>
                <td>{job.entity_type}</td>
                <td className={`mesh-data-jobs__status--${job.status}`}>
                  <span>{t(`dataJobs.status.${job.status}`)}</span>
                  {/* 行级进度(§4.4):文字计数为唯一语义信号,不依赖颜色/脉冲单独表意。 */}
                  {!isTerminalDataJobStatus(job.status) && job.total_rows > 0 ? (
                    <span
                      className="mesh-data-jobs__live-progress"
                      data-testid={`job-progress-${job.id}`}
                    >
                      {t('dataJobs.page.liveProgress', {
                        succeeded: job.succeeded_rows,
                        total: job.total_rows,
                      })}
                    </span>
                  ) : null}
                </td>
                <td>
                  {job.kind === 'import'
                    ? t('dataJobs.page.rowsImport', {
                        succeeded: job.succeeded_rows,
                        failed: job.failed_rows,
                        total: job.total_rows,
                      })
                    : t('dataJobs.page.rowsExport', { total: job.total_rows })}
                </td>
                <td>{job.created_at?.slice(0, 19).replace('T', ' ') ?? ''}</td>
                <td>
                  {job.result_attachment_id !== null && (
                    <Button variant="ghost" size="sm" onClick={() => handleDownload(job)}>
                      {t('dataJobs.page.download')}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {workspace !== null && (
        <>
          <ImportWizard
            open={importOpen}
            onClose={() => {
              setImportOpen(false);
              void load();
            }}
            workspaceId={workspace.id}
          />
          <ExportDialog
            open={exportOpen}
            onClose={() => {
              setExportOpen(false);
              void load();
            }}
            workspaceId={workspace.id}
          />
        </>
      )}
    </div>
  );
}
