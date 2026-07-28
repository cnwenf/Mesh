/**
 * 导入向导(import-export.md §4.2):上传 → 映射 → dry-run 错误 → 确认 → 进度/结果。
 * 分步可回退;上传经 attachment.md 签名直传;进度经 data_job:{id} 实时帧。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { getApiClient } from '../../api';
import type { MeshApiClient } from '../../api';
import { Button } from '../../design/components/Button';
import { Dialog } from '../../design/components/Dialog';
import { useToast } from '../../design/components/Toast';
import { useAttachmentUploader } from '../attachments/useAttachmentUploader';
import { useT } from '../../i18n';
import { errorToI18nKey } from '../../api/errors';
import { MeshApiError } from '../../api';
import { useRealtimeContext } from '../../shell/AppShell';
import {
  createImportJob,
  getDataJob,
  runImportJob,
  validateImportJob,
} from './api';
import { applyDataJobFrame } from './realtime';
import type {
  DataJob,
  DataJobErrorEntry,
  DataJobFormat,
  DataJobMapping,
  MappingColumn,
  TransformType,
} from './types';
import { dataJobChannel, isTerminalDataJobStatus } from './types';
import './dataJobs.css';

type WizardStep = 'upload' | 'mapping' | 'validate' | 'confirm' | 'progress';

const STEP_ORDER: readonly WizardStep[] = ['upload', 'mapping', 'validate', 'confirm', 'progress'];

const TRANSFORM_TYPES: readonly TransformType[] = [
  'direct',
  'value_map',
  'status_by_name',
  'member_by_email',
  'date_parse',
  'list_split',
  'parent_by_external_ref',
];

export interface ImportWizardProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly workspaceId: string;
  readonly targetProjectId?: string | null;
  readonly client?: MeshApiClient;
}

function detectFormat(fileName: string): DataJobFormat {
  return fileName.toLowerCase().endsWith('.json') ? 'json' : 'csv';
}

export function ImportWizard(props: ImportWizardProps): React.JSX.Element | null {
  const { open, onClose, workspaceId, targetProjectId } = props;
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => props.client ?? getApiClient(), [props.client]);
  const realtime = useRealtimeContext();
  const uploader = useAttachmentUploader({ client, workspaceId });

  const [step, setStep] = useState<WizardStep>('upload');
  const [job, setJob] = useState<DataJob | null>(null);
  const [mapping, setMapping] = useState<DataJobMapping | null>(null);
  const [errors, setErrors] = useState<readonly DataJobErrorEntry[]>([]);
  const [busy, setBusy] = useState(false);

  const uploaded = uploader.uploads.find((entry) => entry.phase === 'ready');

  const reset = useCallback(() => {
    setStep('upload');
    setJob(null);
    setMapping(null);
    setErrors([]);
    setBusy(false);
  }, []);

  const handleClose = useCallback(() => {
    reset();
    onClose();
  }, [onClose, reset]);

  /** 进度阶段:订阅 data_job:{id} 实时帧合并进度(§3.11 / §4.2-5)。 */
  useEffect(() => {
    if (step !== 'progress' || job === null || realtime === null) return;
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
  }, [step, job, realtime]);

  /** 进度阶段终态提示(§4.2-6)。 */
  useEffect(() => {
    if (step !== 'progress' || job === null) return;
    if (!isTerminalDataJobStatus(job.status)) return;
    if (job.status === 'failed') {
      toast.addToast(t('dataJobs.import.failedToast'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  }, [step, job, t, toast]);

  const createJob = useCallback(async () => {
    if (uploaded?.attachmentId === null || uploaded?.attachmentId === undefined) return;
    setBusy(true);
    try {
      const created = await createImportJob(
        client,
        {
          workspace_id: workspaceId,
          entity_type: 'issues',
          format: detectFormat(uploaded.fileName),
          source_attachment_id: uploaded.attachmentId,
          auto_infer: mapping === null,
          ...(mapping !== null ? { mapping } : {}),
          ...(targetProjectId != null ? { target_project_id: targetProjectId } : {}),
        },
        crypto.randomUUID(),
      );
      setJob(created);
      setMapping(created.mapping);
      setStep('mapping');
    } catch (error) {
      const key = error instanceof MeshApiError ? errorToI18nKey(error) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    } finally {
      setBusy(false);
    }
  }, [uploaded, client, workspaceId, mapping, targetProjectId, t, toast]);

  const runValidate = useCallback(async () => {
    if (job === null) return;
    setBusy(true);
    try {
      const result = await validateImportJob(client, job.id);
      // validate 是异步动作:轮询到 validating 结束(回置 pending)。
      let current = result;
      for (let i = 0; i < 60 && current.status === 'validating'; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        current = await getDataJob(client, job.id);
      }
      setJob(current);
      setErrors(current.error_report ?? []);
      setStep('validate');
    } catch (error) {
      const key = error instanceof MeshApiError ? errorToI18nKey(error) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    } finally {
      setBusy(false);
    }
  }, [job, client, t, toast]);

  const confirmRun = useCallback(async () => {
    if (job === null) return;
    setBusy(true);
    try {
      const running = await runImportJob(client, job.id);
      setJob(running);
      setStep('progress');
    } catch (error) {
      const key = error instanceof MeshApiError ? errorToI18nKey(error) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    } finally {
      setBusy(false);
    }
  }, [job, client, t, toast]);

  const updateColumnTransform = useCallback((index: number, type: TransformType) => {
    setMapping((current) => {
      if (current === null) return current;
      const columns: MappingColumn[] = current.columns.map((column, i) =>
        i === index ? { ...column, transform: { ...column.transform, type } } : column,
      );
      return { ...current, columns };
    });
  }, []);

  const removeColumn = useCallback((index: number) => {
    setMapping((current) => {
      if (current === null) return current;
      return { ...current, columns: current.columns.filter((_column, i) => i !== index) };
    });
  }, []);

  const stepIndex = STEP_ORDER.indexOf(step);
  const goBack = useCallback(() => {
    setStep((current) => STEP_ORDER[Math.max(0, STEP_ORDER.indexOf(current) - 1)] ?? current);
  }, []);

  const predictedFailed = job?.params?.predicted_failed_rows ?? errors.length;
  const totalRows = job?.total_rows ?? 0;
  const progressPct =
    totalRows > 0 && job !== null
      ? Math.min(100, Math.round(((job.succeeded_rows + job.failed_rows) / totalRows) * 100))
      : 0;

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      title={t('dataJobs.import.title')}
      closeLabel={t('common.close')}
    >
      <ol className="mesh-import-wizard__steps" aria-label={t('dataJobs.import.stepsLabel')}>
        {STEP_ORDER.map((name, i) => (
          <li
            key={name}
            className={i === stepIndex ? 'mesh-import-wizard__step--active' : undefined}
            aria-current={i === stepIndex ? 'step' : undefined}
          >
            {t(`dataJobs.import.step.${name}`)}
          </li>
        ))}
      </ol>

      {step === 'upload' && (
        <section aria-label={t('dataJobs.import.step.upload')}>
          <input
            type="file"
            accept=".csv,.json,text/csv,application/json"
            data-testid="import-file-input"
            onChange={(event) => {
              const files = event.target.files;
              if (files !== null && files.length > 0) uploader.addFiles(files);
            }}
          />
          {uploader.uploads.map((entry) => (
            <p key={entry.localId} data-testid={`upload-${entry.phase}`}>
              {entry.fileName} — {t(`dataJobs.uploadPhase.${entry.phase}`)}
              {entry.phase === 'uploading' ? ` ${Math.round(entry.progress * 100)}%` : ''}
            </p>
          ))}
          <div className="mesh-data-jobs__actions">
            <Button
              variant="primary"
              onClick={() => void createJob()}
              isLoading={busy}
              disabled={uploaded === undefined}
            >
              {t('dataJobs.import.next')}
            </Button>
          </div>
        </section>
      )}

      {step === 'mapping' && mapping !== null && (
        <section aria-label={t('dataJobs.import.step.mapping')}>
          {mapping.columns.map((column, index) => (
            <div className="mesh-import-wizard__mapping-row" key={`${column.source}-${index}`}>
              <span>{column.source}</span>
              <span>{column.target}</span>
              <select
                aria-label={t('dataJobs.import.transformLabel', { column: column.source })}
                value={column.transform.type}
                onChange={(event) =>
                  updateColumnTransform(index, event.target.value as TransformType)
                }
              >
                {TRANSFORM_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => removeColumn(index)}
              >
                {t('dataJobs.import.removeColumn')}
              </Button>
            </div>
          ))}
          <div className="mesh-data-jobs__actions">
            <Button variant="secondary" onClick={goBack}>
              {t('dataJobs.import.back')}
            </Button>
            <Button variant="primary" onClick={() => void runValidate()} isLoading={busy}>
              {t('dataJobs.import.validate')}
            </Button>
          </div>
        </section>
      )}

      {step === 'validate' && (
        <section aria-label={t('dataJobs.import.step.validate')}>
          <p data-testid="validate-summary">
            {t('dataJobs.import.validateSummary', {
              total: totalRows,
              importable: Math.max(0, totalRows - predictedFailed),
              skipped: predictedFailed,
            })}
          </p>
          {errors.length > 0 && (
            <div className="mesh-import-wizard__errors">
              <table>
                <thead>
                  <tr>
                    <th>{t('dataJobs.import.errorRow')}</th>
                    <th>{t('dataJobs.import.errorField')}</th>
                    <th>{t('dataJobs.import.errorCode')}</th>
                    <th>{t('dataJobs.import.errorMessage')}</th>
                  </tr>
                </thead>
                <tbody>
                  {errors.map((entry) => (
                    <tr key={`${entry.row}-${entry.field}-${entry.code}`}>
                      <td>{entry.row}</td>
                      <td>{entry.field}</td>
                      <td>{entry.code}</td>
                      <td>{entry.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="mesh-data-jobs__actions">
            <Button variant="secondary" onClick={goBack}>
              {t('dataJobs.import.back')}
            </Button>
            <Button variant="primary" onClick={() => setStep('confirm')}>
              {t('dataJobs.import.next')}
            </Button>
          </div>
        </section>
      )}

      {step === 'confirm' && (
        <section aria-label={t('dataJobs.import.step.confirm')}>
          <p>
            {t('dataJobs.import.confirmSummary', {
              importable: Math.max(0, totalRows - predictedFailed),
              skipped: predictedFailed,
            })}
          </p>
          <div className="mesh-data-jobs__actions">
            <Button variant="secondary" onClick={goBack}>
              {t('dataJobs.import.back')}
            </Button>
            <Button
              variant="primary"
              onClick={() => void confirmRun()}
              isLoading={busy}
              data-testid="confirm-import-button"
            >
              {t('dataJobs.import.confirm')}
            </Button>
          </div>
        </section>
      )}

      {step === 'progress' && job !== null && (
        <section aria-label={t('dataJobs.import.step.progress')}>
          <div className="mesh-data-jobs__progress">
            <div
              className="mesh-data-jobs__progress-bar"
              role="progressbar"
              aria-valuenow={progressPct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className="mesh-data-jobs__progress-fill" style={{ width: `${progressPct}%` }} />
            </div>
            <span data-testid="progress-count">
              {t('dataJobs.import.progressCount', {
                succeeded: job.succeeded_rows,
                failed: job.failed_rows,
                total: Math.max(totalRows, job.succeeded_rows + job.failed_rows),
              })}
            </span>
          </div>
          <p data-testid="progress-status">{t(`dataJobs.status.${job.status}`)}</p>
          {job.status === 'failed' && job.failure_reason !== null && (
            <p>{t('dataJobs.import.failureReason', { reason: job.failure_reason })}</p>
          )}
          {isTerminalDataJobStatus(job.status) && job.result_attachment_id !== null && (
            <a href={`/api/v1/data-jobs/${job.id}/download`} data-testid="error-report-link">
              {t('dataJobs.import.downloadReport')}
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
