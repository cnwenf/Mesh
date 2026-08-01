/**
 * 数据管理页(import-export.md §4.1):设置 → 数据管理(管理员区)。
 * 作业列表(导入/导出历史、状态、计数、重新下载)+ 导入/导出主入口。
 * 异常态矩阵(§6.12):错误态可重试 / 骨架 / 空态。
 */
import { useCallback, useEffect, useState } from 'react';
import { useIntl } from 'react-intl';
import { useParams } from 'react-router';

import { getApiClient } from '../../api';
import { Button, EmptyState, ErrorState, SettingsSection, Skeleton, useToast } from '../../design';
import { formatDateTime, useT } from '../../i18n';
import { useWorkspace } from '../../workspace/WorkspaceProvider';
import { listDataJobs } from './api';
import { ExportDialog } from './ExportDialog';
import { ImportWizard } from './ImportWizard';
import type { DataJob } from './types';
import './dataJobs.css';

function formatCreatedAt(value: string | null | undefined, locale: string): string {
  if (value === null || value === undefined || value === '') return '';
  try {
    return formatDateTime(value, {
      locale,
      timeZone: 'UTC',
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  } catch {
    return value;
  }
}

export function DataManagementPage(): React.JSX.Element {
  const t = useT();
  const intl = useIntl();
  const toast = useToast();
  const { workspaceSlug } = useParams();
  const { workspace, isAdmin } = useWorkspace();
  const client = getApiClient();

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
    <SettingsSection
      title={t('dataJobs.page.title')}
      description={t('dataJobs.page.subtitle', { workspace: workspaceSlug ?? '' })}
    >
      <div className="mesh-data-jobs__page" data-testid="data-management-section">
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
            impact={t('dataJobs.page.loadImpact')}
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
          <div className="mesh-data-jobs__table-scroll" data-testid="data-jobs-table-scroll">
            <table className="mesh-data-jobs__table" aria-label={t('dataJobs.page.tableLabel')}>
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
                      {t(`dataJobs.status.${job.status}`)}
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
                    <td>
                      <time dateTime={job.created_at ?? undefined}>
                        {formatCreatedAt(job.created_at, intl.locale)}
                      </time>
                    </td>
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
          </div>
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
    </SettingsSection>
  );
}
