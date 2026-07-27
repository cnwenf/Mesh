/**
 * 导入向导(skill.md §4.1/§4.3):① 选择来源 → ② 预览校验(含脚本强制逐项确认 +
 * 权限最小化勾选) → ③ 审批安装。进度经 GET import/{task_id} 轮询(3~5s 退化方案,
 * §3.5);含脚本的 marketplace/url 来源强制人工审阅,未审批不得安装(§5.3)。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { MeshApiClient, getToken } from '../../api';
import { Button, Dialog, Input, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { workspaceSkillsChannel } from './api';
import { approveSkill, installSkill, startImport } from './api';
import type { ImportTask } from './types';

/** 高危能力关键词(§4.2 网络/写文件等高亮)——与详情页脚本 Tab 复用同一规则。 */
const RISKY_CAPABILITY_PATTERN = /net:|write|exec:/i;

const POLL_INTERVAL_MS = 4000;

type Step = 'source' | 'preview' | 'install' | 'done';

export function ImportWizard({
  workspaceId,
  onClose,
  onDone,
  initialUri,
  initialSourceType,
}: {
  workspaceId: string;
  onClose: () => void;
  onDone: () => void;
  initialUri?: string;
  initialSourceType?: 'marketplace' | 'url';
}): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const [step, setStep] = useState<Step>(initialUri !== undefined ? 'preview' : 'source');
  const [sourceType, setSourceType] = useState<'marketplace' | 'url'>(initialSourceType ?? 'url');
  const [uri, setUri] = useState(initialUri ?? '');
  const [task, setTask] = useState<ImportTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmedScripts, setConfirmedScripts] = useState<Set<string>>(new Set());
  const [granted, setGranted] = useState<Set<string>>(new Set());
  const [comment, setComment] = useState('');
  const [autoUpdate, setAutoUpdate] = useState(false);

  /** 阶段推进轮询(§3.5 无 WebSocket 退化方案;有 WS 时同样可读 REST 真源)。 */
  useEffect(() => {
    if (task === null) return;
    if (!['parsing', 'validating', 'sandbox_preview', 'installing'].includes(task.status)) return;
    const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
    const timer = setInterval(() => {
      void (async () => {
        try {
          const fresh = await (
            await import('./api')
          ).getImportTask(client, workspaceId, task.task_id);
          setTask(fresh);
        } catch {
          /* 轮询瞬时失败忽略,下轮重试 */
        }
      })();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [task, workspaceId]);

  // M2: realtime is the PRIMARY progress channel (§3.5); REST polling above is
  // the §4.6 fallback. A skill_import.progress frame for this task updates the
  // bar immediately without waiting for the next poll.
  const realtime = useRealtimeContext();
  useEffect(() => {
    if (realtime === null || task === null) return;
    const channel = workspaceSkillsChannel(workspaceId);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (String(frame.channel) !== channel || frame.event !== 'skill_import.progress') return;
      const data = frame.payload as { task_id?: string; percent?: number; stage?: string; status?: string };
      if (data.task_id !== task.task_id) return;
      setTask((prev) =>
        prev === null
          ? prev
          : {
              ...prev,
              percent: typeof data.percent === 'number' ? data.percent : prev.percent,
              stage: data.stage ?? prev.stage,
              status: (data.status ?? prev.status) as ImportTask['status'],
            },
      );
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, task, workspaceId]);

  // 预览就绪时,默认最小化授权:建议拒绝项(高危)预置不勾选(§4.2)。
  useEffect(() => {
    if (task?.preview === undefined || task.preview === null) return;
    setGranted(new Set());
    setConfirmedScripts(new Set());
  }, [task?.preview]);

  const start = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
      const created = await startImport(client, workspaceId, {
        source_type: sourceType,
        uri: uri.trim(),
      });
      setTask(created);
      if (['awaiting_review', 'ready'].includes(created.status)) {
        setStep('preview');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('skills.importStartFailed'));
    } finally {
      setBusy(false);
    }
  }, [sourceType, uri, workspaceId, t]);

  // When opened with a known URI (marketplace entry), kick off the fetch once.
  const autoStarted = useRef(false);
  useEffect(() => {
    if (initialUri !== undefined && !autoStarted.current) {
      autoStarted.current = true;
      void start();
    }
  }, [initialUri, start]);

  const preview = task?.preview ?? null;
  const scripts = preview?.scripts ?? [];
  const allScriptsConfirmed = scripts.length === 0 || scripts.every((s) => confirmedScripts.has(s.path));
  const requested = preview?.requested_capabilities ?? [];

  const approve = useCallback(
    async (decision: 'approve' | 'reject') => {
      if (task === null || task.skill_id === null) return;
      setBusy(true);
      setError(null);
      try {
        const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
        const updated = await approveSkill(client, workspaceId, task.skill_id, {
          task_id: task.task_id,
          granted_capabilities: [...granted],
          decision,
          comment: comment.trim() === '' ? null : comment.trim(),
        });
        setTask(updated);
        if (decision === 'reject') {
          toast.addToast(t('skills.importRejected'), { tone: 'info', closeLabel: t('a11y.closeDialog') });
          onDone();
          return;
        }
        setStep('install');
      } catch (err) {
        setError(err instanceof Error ? err.message : t('skills.approveFailed'));
      } finally {
        setBusy(false);
      }
    },
    [task, workspaceId, granted, comment, t, toast, onDone],
  );

  const install = useCallback(async () => {
    if (task === null || task.skill_id === null || task.skill_version_id === null) return;
    setBusy(true);
    setError(null);
    try {
      const client = new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken });
      await installSkill(client, workspaceId, {
        skill_id: task.skill_id,
        skill_version_id: task.skill_version_id,
        scope: 'workspace',
        auto_update: autoUpdate,
      });
      setStep('done');
      toast.addToast(t('skills.installSucceeded'), { tone: 'success', closeLabel: t('a11y.closeDialog') });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('skills.installFailed'));
    } finally {
      setBusy(false);
    }
  }, [task, workspaceId, autoUpdate, t, toast, onDone]);

  return (
    <Dialog open title={t('skills.importTitle')} onClose={onClose} closeLabel={t('a11y.closeDialog')}>
      <div className="mesh-skills-wizard">
        <ol className="mesh-skills-wizard__steps">
          <li className={step === 'source' ? 'is-active' : ''}>{t('skills.importStepSource')}</li>
          <li className={step === 'preview' ? 'is-active' : ''}>{t('skills.importStepPreview')}</li>
          <li className={step === 'install' ? 'is-active' : ''}>{t('skills.importStepInstall')}</li>
        </ol>

        {step === 'source' ? (
          <div className="mesh-skills-wizard__body" data-testid="import-step-source">
            <label className="mesh-skills__field">
              {t('skills.importSourceType')}
              <select aria-label={t('skills.importSourceType')} value={sourceType} onChange={(e) => setSourceType(e.target.value as 'url' | 'marketplace')}>
                <option value="url">{t('skills.source.url')}</option>
                <option value="marketplace">{t('skills.source.marketplace')}</option>
              </select>
            </label>
            <Input label={t('skills.importUri')} value={uri} onChange={(e) => setUri(e.target.value)} data-testid="import-uri" />
            {error !== null ? <p className="mesh-skills__form-error" data-testid="import-start-error">{error}</p> : null}
            <div className="mesh-skills__form-actions">
              <Button variant="secondary" onClick={onClose}>{t('skills.cancel')}</Button>
              <Button
                onClick={() => void start()}
                disabled={busy || uri.trim() === ''}
                data-testid="import-start"
              >
                {busy ? t('skills.importRunning') : t('skills.importStart')}
              </Button>
            </div>
          </div>
        ) : null}

        {step === 'preview' ? (
          <div className="mesh-skills-wizard__body" data-testid="import-step-preview">
            {task !== null && ['parsing', 'validating', 'sandbox_preview', 'installing'].includes(task.status) ? (
              <div className="mesh-skills-wizard__progress" data-testid="import-progress">
                <span className="mesh-skills-wizard__progress-label">
                  {t('skills.importProgress', { name: `${task.stage} · ${task.percent}%` })}
                </span>
                <span
                  className="mesh-skills-wizard__progress-bar"
                  role="progressbar"
                  aria-valuenow={task.percent}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <span style={{ width: `${Math.min(100, Math.max(0, task.percent))}%` }} />
                </span>
              </div>
            ) : null}
            {task === null ? (
              <p data-testid="import-starting">{t('skills.importRunning')}</p>
            ) : null}
            {task?.status === 'failed' ? (
              <p className="mesh-skills__form-error" data-testid="import-failed">
                {t('skills.importFailed')}: {task.error ?? ''}
              </p>
            ) : null}
            {preview !== null && task !== null ? (
              <>
                <h3>{preview.name} <span className="mesh-skills__version-tag">v{preview.version}</span></h3>
                <p className="mesh-skills-wizard__summary">{preview.summary}</p>
                <pre className="mesh-skills-wizard__instructions">{preview.instructions_preview}</pre>

                {scripts.length > 0 ? (
                  <section className="mesh-skills-wizard__scripts" data-testid="import-scripts">
                    <h4>{t('skills.importScriptsTitle')}</h4>
                    <p className="mesh-skills-wizard__warn">{t('skills.importScriptsWarn')}</p>
                    {scripts.map((script) => (
                      <label key={script.path} className="mesh-skills-wizard__script">
                        <input
                          type="checkbox"
                          checked={confirmedScripts.has(script.path)}
                          onChange={(e) => {
                            setConfirmedScripts((prev) => {
                              const next = new Set(prev);
                              if (e.target.checked) next.add(script.path);
                              else next.delete(script.path);
                              return next;
                            });
                          }}
                          data-testid={`import-confirm-${script.path}`}
                        />
                        <code>{script.path}</code>
                        <span className="mesh-skills-wizard__script-runtime">{script.runtime}</span>
                        {script.entrypoint ? <span className="mesh-skills-wizard__entry">entry</span> : null}
                        <span className="mesh-skills-wizard__caps">
                          {(script.required_capabilities ?? []).map((cap, ci) => {
                            const key = typeof cap === 'string' ? cap : cap.capability;
                            return (
                              <span
                                key={`${key}-${ci}`}
                                className={RISKY_CAPABILITY_PATTERN.test(key) ? 'is-risky' : ''}
                              >
                                {key}
                              </span>
                            );
                          })}
                        </span>
                      </label>
                    ))}
                  </section>
                ) : null}

                {requested.length > 0 ? (
                  <section className="mesh-skills-wizard__caps-section" data-testid="import-capabilities">
                    <h4>{t('skills.importCapsTitle')}</h4>
                    <p className="mesh-skills-wizard__caps-hint">{t('skills.importCapsHint')}</p>
                    {requested.map((cap) => (
                      <label
                        key={cap}
                        className={`mesh-skills-wizard__cap${RISKY_CAPABILITY_PATTERN.test(cap) ? ' is-risky' : ''}`}
                      >
                        <input
                          type="checkbox"
                          checked={granted.has(cap)}
                          onChange={(e) => {
                            setGranted((prev) => {
                              const next = new Set(prev);
                              if (e.target.checked) next.add(cap);
                              else next.delete(cap);
                              return next;
                            });
                          }}
                          data-testid={`import-grant-${cap}`}
                        />
                        {cap}
                      </label>
                    ))}
                  </section>
                ) : null}

                {task.requires_approval ? (
                  <Input label={t('skills.importComment')} value={comment} onChange={(e) => setComment(e.target.value)} />
                ) : null}

                {error !== null ? <p className="mesh-skills__form-error">{error}</p> : null}
                <div className="mesh-skills__form-actions">
                  <Button variant="secondary" onClick={onClose}>{t('skills.cancel')}</Button>
                  {task.requires_approval ? (
                    <>
                      <Button
                        variant="secondary"
                        onClick={() => void approve('reject')}
                        disabled={busy}
                        data-testid="import-reject"
                      >
                        {t('skills.importReject')}
                      </Button>
                      <Button
                        onClick={() => void approve('approve')}
                        disabled={busy || !allScriptsConfirmed}
                        data-testid="import-approve"
                      >
                        {t('skills.importApprove')}
                      </Button>
                    </>
                  ) : (
                    <Button
                      onClick={() => setStep('install')}
                      disabled={task.status !== 'ready'}
                      data-testid="import-to-install"
                    >
                      {t('skills.importNext')}
                    </Button>
                  )}
                </div>
              </>
            ) : null}
          </div>
        ) : null}

        {step === 'install' ? (
          <div className="mesh-skills-wizard__body" data-testid="import-step-install">
            <p>{t('skills.importInstallHint')}</p>
            <label className="mesh-skills-wizard__cap">
              <input
                type="checkbox"
                checked={autoUpdate}
                onChange={(e) => setAutoUpdate(e.target.checked)}
                data-testid="import-auto-update"
              />
              {t('skills.importAutoUpdate')}
            </label>
            {error !== null ? <p className="mesh-skills__form-error">{error}</p> : null}
            <div className="mesh-skills__form-actions">
              <Button variant="secondary" onClick={onClose}>{t('skills.cancel')}</Button>
              <Button onClick={() => void install()} disabled={busy} data-testid="import-install">
                {t('skills.importInstall')}
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
