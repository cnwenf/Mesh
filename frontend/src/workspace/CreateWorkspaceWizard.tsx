/**
 * 创建工作区向导(workspace.md §4.2/§4.3):名称 → slug(实时格式校验 + 占用探测)→ 可选邀请 → 完成。
 *
 * 完成后自动进入新工作区(创建者成 owner);邀请为 best-effort(失败仅提示,不阻塞建区)。
 * 409 slug_taken / 400 validation_error 具名呈现(§6.14)。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:组件与同域纯函数/常量同文件共存 */
import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router';
import type { MeshApiClient } from '../api/client';
import { MeshApiError } from '../api/errors';
import { getApiClient } from '../api/instance';
import { createInvitations, createWorkspace, getWorkspaceBySlug } from '../api';
import { Button, Dialog, Input, useToast } from '../design';
import { errorToI18nKey } from '../api/errors';
import { useT } from '../i18n';
import { EmailChipsInput } from './EmailChipsInput';
import { isValidSlug } from './permissions';

type WizardStep = 'name' | 'slug' | 'invite';

/** 由名称生成 slug 建议(小写、非字母数字转连字符、≤32) */
export function suggestSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32);
}

export interface CreateWorkspaceWizardProps {
  open: boolean;
  onClose(): void;
  client?: MeshApiClient;
}

export function CreateWorkspaceWizard(props: CreateWorkspaceWizardProps): React.JSX.Element {
  const { open, onClose } = props;
  const client = props.client ?? getApiClient();
  const t = useT();
  const navigate = useNavigate();
  const { addToast } = useToast();

  const [step, setStep] = useState<WizardStep>('name');
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [emails, setEmails] = useState<string[]>([]);
  const [slugState, setSlugState] = useState<'idle' | 'valid' | 'invalid' | 'taken'>('idle');
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const reset = useCallback((): void => {
    setStep('name');
    setName('');
    setSlug('');
    setEmails([]);
    setSlugState('idle');
    setErrorKey(null);
    setIsSubmitting(false);
  }, []);

  const handleClose = (): void => {
    reset();
    onClose();
  };

  const nameValid = name.trim().length >= 1 && name.trim().length <= 80;

  const goToSlugStep = (): void => {
    const suggested = suggestSlug(name.trim());
    setSlug((current) => (current.length > 0 ? current : suggested));
    setSlugState('idle');
    setErrorKey(null);
    setStep('slug');
  };

  const handleSlugChange = (value: string): void => {
    setSlug(value);
    setErrorKey(null);
    setSlugState(value.length === 0 ? 'idle' : isValidSlug(value) ? 'valid' : 'invalid');
  };

  /** slug 占用探测:by-slug 200 = 已占用;404 = 可用(最终占用以后端创建 409 为准) */
  const goToInviteStep = async (): Promise<void> => {
    if (!isValidSlug(slug)) {
      setSlugState('invalid');
      return;
    }
    try {
      await getWorkspaceBySlug(client, slug);
      setSlugState('taken');
      setErrorKey('error.slug_taken');
    } catch (err) {
      if (err instanceof MeshApiError && err.status === 404) {
        setStep('invite');
        return;
      }
      setErrorKey(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown');
    }
  };

  const finish = async (): Promise<void> => {
    setIsSubmitting(true);
    setErrorKey(null);
    try {
      const workspace = await createWorkspace(client, { name: name.trim(), slug });
      if (emails.length > 0) {
        try {
          await createInvitations(client, workspace.id, { emails, role: 'member' });
        } catch {
          addToast(t('wsCreate.inviteFailedToast'), {
            tone: 'warn',
            closeLabel: t('a11y.dismiss'),
          });
        }
      }
      reset();
      onClose();
      navigate(`/w/${workspace.slug}`);
    } catch (err) {
      setIsSubmitting(false);
      if (err instanceof MeshApiError && err.code === 'slug_taken') {
        setSlugState('taken');
        setStep('slug');
      }
      setErrorKey(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown');
    }
  };

  const title =
    step === 'name'
      ? t('wsCreate.stepName')
      : step === 'slug'
        ? t('wsCreate.stepSlug')
        : t('wsCreate.stepInvite');

  return (
    <Dialog open={open} onClose={handleClose} title={title} closeLabel={t('a11y.closeDialog')}>
      {step === 'name' ? (
        <div className="mesh-wizard" data-testid="ws-wizard-name">
          <Input
            label={t('wsCreate.nameLabel')}
            value={name}
            data-testid="ws-wizard-name-input"
            onChange={(event) => setName(event.target.value)}
          />
          <Button
            data-testid="ws-wizard-next"
            disabled={!nameValid}
            onClick={goToSlugStep}
          >
            {t('wsCreate.next')}
          </Button>
        </div>
      ) : null}
      {step === 'slug' ? (
        <div className="mesh-wizard" data-testid="ws-wizard-slug">
          <Input
            label={t('wsCreate.slugLabel')}
            value={slug}
            hint={t('wsCreate.slugHint')}
            error={
              slugState === 'invalid'
                ? t('wsCreate.slugInvalid')
                : slugState === 'taken'
                  ? t('wsCreate.slugTaken')
                  : undefined
            }
            data-testid="ws-wizard-slug-input"
            onChange={(event) => handleSlugChange(event.target.value)}
          />
          <span data-testid="ws-wizard-slug-check" aria-live="polite">
            {slugState === 'valid'
              ? t('wsCreate.slugAvailable')
              : slugState === 'taken'
                ? t('wsCreate.slugTaken')
                : ''}
          </span>
          {errorKey !== null ? (
            <p role="alert" data-testid="ws-wizard-error">
              {t(errorKey)}
            </p>
          ) : null}
          <Button
            data-testid="ws-wizard-next-slug"
            disabled={!isValidSlug(slug)}
            onClick={() => void goToInviteStep()}
          >
            {t('wsCreate.next')}
          </Button>
        </div>
      ) : null}
      {step === 'invite' ? (
        <div className="mesh-wizard" data-testid="ws-wizard-invite">
          <EmailChipsInput
            label={t('wsCreate.inviteLabel')}
            emails={emails}
            onChange={setEmails}
            placeholder={t('wsCreate.invitePlaceholder')}
            invalidFormatHint={t('wsCreate.inviteInvalid')}
            maxCountHint={t('wsCreate.inviteTooMany')}
            removeLabel={t('wsCreate.inviteRemove')}
          />
          {errorKey !== null ? (
            <p role="alert" data-testid="ws-wizard-error">
              {t(errorKey)}
            </p>
          ) : null}
          <Button
            variant="secondary"
            data-testid="ws-wizard-skip"
            onClick={() => void finish()}
            isLoading={isSubmitting}
          >
            {t('wsCreate.skipInvite')}
          </Button>
          <Button
            data-testid="ws-wizard-create"
            onClick={() => void finish()}
            isLoading={isSubmitting}
          >
            {t('wsCreate.finish')}
          </Button>
        </div>
      ) : null}
    </Dialog>
  );
}
