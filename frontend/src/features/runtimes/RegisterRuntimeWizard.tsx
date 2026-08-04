/**
 * 注册新 runtime 三段式引导(runtime.md §4.3):
 *   1) 基本信息(名称 / 类型 / 并发上限 / 标签 key=value 增删);
 *   2) 安装说明——由 createRuntime 响应的 activation.release + code 生成:
 *      下载签名发布包 → 校验 sha256 + 签名 → 解包 → 受限文件激活。
 *      无 `curl | sh` 管道,命令逐条可审;激活码提示「0600 文件、用后即毁」;
 *   3) 等待激活:订阅 workspace:{ws}:runtimes,runtime.activated 帧到达即 ⏳→✅,
 *      给出详情页深链(§4.3 要点;无实时连接时保留手动前往详情的路径)。
 */
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router';
import type { MeshApiClient } from '../../api';
import { MeshApiError, errorToI18nKey } from '../../api/errors';
import { Button, Dialog, Input, Select, useToast } from '../../design';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { workspaceRoute } from '../members/useWorkspaceMembership';
import { createRuntime, workspaceRuntimesChannel } from './api';
import { buildInstallScript } from './format';
import type { RuntimeKind, RuntimeWithActivation } from './types';
import './runtimes.css';

type WizardStep = 'basic' | 'install' | 'waiting';

const STEP_ORDER: readonly WizardStep[] = ['basic', 'install', 'waiting'];

const NAME_MAX = 120;
const MAX_CONCURRENT_MIN = 1;
const MAX_CONCURRENT_MAX = 64;

interface LabelRow {
  readonly key: string;
  readonly value: string;
}

export interface RegisterRuntimeWizardProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  /** Canonical route slug; omitted only by legacy flat-route tests/callers. */
  readonly workspaceSlug?: string;
  /** 注册成功(创建影子记录)后回调,供列表页重拉。 */
  readonly onRegistered: (runtime: RuntimeWithActivation) => void;
}

/** 标签行 → Record;重复键 / 空键返回 null(调用方拦截下一步)。 */
function labelsToRecord(rows: readonly LabelRow[]): Record<string, string> | null {
  const record: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (key === '' && row.value.trim() === '') continue; // 全空行视为未填
    if (key === '' || Object.prototype.hasOwnProperty.call(record, key)) return null;
    record[key] = row.value.trim();
  }
  return record;
}

export function RegisterRuntimeWizard(props: RegisterRuntimeWizardProps): React.JSX.Element {
  const { open, onClose, client, workspaceId, workspaceSlug, onRegistered } = props;
  const t = useT();
  const toast = useToast();
  const realtime = useRealtimeContext();

  const [step, setStep] = useState<WizardStep>('basic');
  const [name, setName] = useState('');
  const [kind, setKind] = useState<RuntimeKind>('self_hosted');
  const [maxConcurrent, setMaxConcurrent] = useState('1');
  const [labelRows, setLabelRows] = useState<readonly LabelRow[]>([{ key: '', value: '' }]);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [created, setCreated] = useState<RuntimeWithActivation | null>(null);
  const [activated, setActivated] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (open) {
      setStep('basic');
      setName('');
      setKind('self_hosted');
      setMaxConcurrent('1');
      setLabelRows([{ key: '', value: '' }]);
      setErrorKey(null);
      setIsSubmitting(false);
      setCreated(null);
      setActivated(false);
      setCopied(false);
    }
  }, [open]);

  // 等待步:监听 runtime.activated(§4.3 第 3 步 ⏳→✅,无需手动刷新)。
  useEffect(() => {
    if (!open || step !== 'waiting' || created === null || realtime === null) return;
    const channel = workspaceRuntimesChannel(workspaceId);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel || frame.event !== 'runtime.activated') return;
      const payload = frame.payload as { data?: { id?: string }; id?: string };
      const frameId = payload.data?.id ?? payload.id;
      if (frameId !== created.id) return;
      setActivated(true);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [open, step, created, realtime, workspaceId]);

  const patchLabelRow = useCallback((index: number, partial: Partial<LabelRow>): void => {
    setLabelRows((rows) => rows.map((row, i) => (i === index ? { ...row, ...partial } : row)));
  }, []);

  const addLabelRow = (): void => {
    setLabelRows((rows) => [...rows, { key: '', value: '' }]);
  };

  const removeLabelRow = (index: number): void => {
    setLabelRows((rows) => rows.filter((_, i) => i !== index));
  };

  const nameValid = name.trim().length >= 1 && name.trim().length <= NAME_MAX;
  const maxConcurrentNum = Number(maxConcurrent);
  const maxConcurrentValid =
    maxConcurrent !== '' &&
    Number.isInteger(maxConcurrentNum) &&
    maxConcurrentNum >= MAX_CONCURRENT_MIN &&
    maxConcurrentNum <= MAX_CONCURRENT_MAX;
  const labelsValid = labelsToRecord(labelRows) !== null;
  const basicValid = nameValid && maxConcurrentValid && labelsValid;

  const submitBasic = async (): Promise<void> => {
    setIsSubmitting(true);
    setErrorKey(null);
    try {
      const result = await createRuntime(client, workspaceId, {
        name: name.trim(),
        kind,
        max_concurrent: maxConcurrentNum,
        labels: labelsToRecord(labelRows) ?? {},
      });
      setCreated(result);
      onRegistered(result);
      setStep('install');
    } catch (err) {
      setErrorKey(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown');
    } finally {
      setIsSubmitting(false);
    }
  };

  const copyToClipboard = async (text: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.addToast(t('runtimes.wizard.copied'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch {
      toast.addToast(t('runtimes.wizard.copyFailed'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const stepIndex = STEP_ORDER.indexOf(step);
  const installScript =
    created !== null ? buildInstallScript(created.activation.release, created.activation.code) : '';

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t('runtimes.wizard.title')}
      closeLabel={t('a11y.closeDialog')}
    >
      <div className="mesh-runtimes-wizard">
        <ol className="mesh-runtimes-wizard__steps" aria-label={t('runtimes.wizard.progress')}>
          {STEP_ORDER.map((key, index) => (
            <li
              key={key}
              className={
                index === stepIndex
                  ? 'mesh-runtimes-wizard__step mesh-runtimes-wizard__step--active'
                  : 'mesh-runtimes-wizard__step'
              }
              aria-current={index === stepIndex ? 'step' : undefined}
              data-testid={`runtime-wizard-step-${key}`}
            >
              {t(`runtimes.wizard.step.${key}`)}
            </li>
          ))}
        </ol>

        {step === 'basic' ? (
          <div className="mesh-runtimes-wizard__body" data-testid="runtime-wizard-basic">
            <Input
              label={t('runtimes.field.name')}
              value={name}
              data-testid="runtime-wizard-name"
              error={
                name.trim().length > NAME_MAX ? t('runtimes.validation.nameTooLong') : undefined
              }
              onChange={(event) => {
                setName(event.target.value);
                setErrorKey(null);
              }}
            />
            <Select
              label={t('runtimes.field.kind')}
              value={kind}
              data-testid="runtime-wizard-kind"
              onChange={(event) => setKind(event.target.value as RuntimeKind)}
            >
              <option value="self_hosted">{t('runtimes.kind.self_hosted')}</option>
              <option value="platform_managed">{t('runtimes.kind.platform_managed')}</option>
            </Select>
            <Input
              label={t('runtimes.field.maxConcurrent')}
              value={maxConcurrent}
              data-testid="runtime-wizard-max-concurrent"
              error={!maxConcurrentValid ? t('runtimes.validation.maxConcurrent') : undefined}
              onChange={(event) => setMaxConcurrent(event.target.value)}
            />
            <div className="mesh-runtimes-wizard__labels">
              <p className="mesh-runtimes-wizard__label">{t('runtimes.field.labels')}</p>
              {labelRows.map((row, index) => (
                <div key={index} className="mesh-runtimes-wizard__label-row">
                  <input
                    type="text"
                    aria-label={t('runtimes.field.labelKey')}
                    value={row.key}
                    placeholder="gpu"
                    data-testid={`runtime-wizard-label-key-${index}`}
                    onChange={(event) => patchLabelRow(index, { key: event.target.value })}
                  />
                  <span aria-hidden="true">=</span>
                  <input
                    type="text"
                    aria-label={t('runtimes.field.labelValue')}
                    value={row.value}
                    placeholder="true"
                    data-testid={`runtime-wizard-label-value-${index}`}
                    onChange={(event) => patchLabelRow(index, { value: event.target.value })}
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    data-testid={`runtime-wizard-label-remove-${index}`}
                    disabled={labelRows.length <= 1}
                    onClick={() => removeLabelRow(index)}
                  >
                    {t('runtimes.wizard.removeLabel')}
                  </Button>
                </div>
              ))}
              <Button
                variant="secondary"
                size="sm"
                data-testid="runtime-wizard-label-add"
                onClick={addLabelRow}
              >
                {t('runtimes.wizard.addLabel')}
              </Button>
              {!labelsValid ? (
                <p
                  role="alert"
                  className="mesh-runtimes-wizard__error"
                  data-testid="runtime-wizard-labels-error"
                >
                  {t('runtimes.validation.labelsInvalid')}
                </p>
              ) : null}
            </div>
          </div>
        ) : null}

        {step === 'install' && created !== null ? (
          <div className="mesh-runtimes-wizard__body" data-testid="runtime-wizard-install">
            <p className="mesh-runtimes-wizard__hint">{t('runtimes.wizard.installIntro')}</p>
            <div className="mesh-runtimes-wizard__code-head">
              <span className="mesh-runtimes-wizard__label">
                {t('runtimes.wizard.activationCode')}
              </span>
              <Button
                variant="secondary"
                size="sm"
                data-testid="runtime-wizard-copy"
                onClick={() => void copyToClipboard(installScript)}
              >
                {copied ? t('runtimes.wizard.copied') : t('runtimes.wizard.copy')}
              </Button>
            </div>
            <code
              className="mesh-runtimes-wizard__activation-code"
              data-testid="runtime-wizard-activation-code"
            >
              {created.activation.code}
            </code>
            <p className="mesh-runtimes-wizard__secret-hint">
              {t('runtimes.wizard.activationHint')}
            </p>
            <pre className="mesh-runtimes-wizard__pre" data-testid="runtime-wizard-install-script">
              {installScript}
            </pre>
            <p className="mesh-runtimes-wizard__expires">
              {t('runtimes.wizard.expiresAt', { when: created.activation.expires_at })}
            </p>
          </div>
        ) : null}

        {step === 'waiting' && created !== null ? (
          <div className="mesh-runtimes-wizard__body" data-testid="runtime-wizard-waiting">
            {activated ? (
              <p
                className="mesh-runtimes-wizard__waiting mesh-runtimes-wizard__waiting--done"
                data-testid="runtime-wizard-activated"
              >
                {t('runtimes.wizard.activated', { name: created.name })}
              </p>
            ) : (
              <p className="mesh-runtimes-wizard__waiting" data-testid="runtime-wizard-pending">
                {t('runtimes.wizard.waiting')}
              </p>
            )}
            <Link
              className="mesh-runtimes-wizard__link"
              to={
                workspaceSlug === undefined
                  ? `/runtimes/${created.id}`
                  : workspaceRoute(workspaceSlug, `/automations/runtimes/${created.id}`)
              }
              data-testid="runtime-wizard-detail-link"
            >
              {t('runtimes.wizard.goDetail')}
            </Link>
          </div>
        ) : null}

        {errorKey !== null ? (
          <p
            role="alert"
            className="mesh-runtimes-wizard__error"
            data-testid="runtime-wizard-error"
          >
            {t(errorKey)}
          </p>
        ) : null}

        <div className="mesh-runtimes-wizard__footer">
          {step === 'basic' ? (
            <Button
              data-testid="runtime-wizard-next"
              disabled={!basicValid}
              isLoading={isSubmitting}
              onClick={() => void submitBasic()}
            >
              {t('runtimes.wizard.create')}
            </Button>
          ) : null}
          {step === 'install' ? (
            <Button data-testid="runtime-wizard-to-waiting" onClick={() => setStep('waiting')}>
              {t('runtimes.wizard.installed')}
            </Button>
          ) : null}
          {step === 'waiting' ? (
            <Button variant="secondary" data-testid="runtime-wizard-done" onClick={onClose}>
              {t('common.close')}
            </Button>
          ) : null}
        </div>
      </div>
    </Dialog>
  );
}
