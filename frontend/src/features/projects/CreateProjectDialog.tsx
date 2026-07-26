/**
 * 新建项目对话框(§4.3):名称 → 自动建议大写 key(可改)→ 客户端格式即时校验
 * (绿勾/红叉文案)→ 可见性/目标日 → 提交。409 project_key_taken/project_name_taken
 * 经 errorToI18nKey 就地内联;成功后 toast + 回调重载列表。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { MeshApiError, errorToI18nKey } from '../../api';
import type { MeshApiClient } from '../../api';
import { Button, Dialog, Input, Select, useToast } from '../../design';
import { useT } from '../../i18n';
import { createProject } from './api';
import { isValidProjectKey, suggestProjectKey } from './helpers';
import { LabeledTextarea } from './widgets';
import type { ProjectVisibility } from './types';

export interface CreateProjectDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  /** 创建成功后的回调(传入新项目 id,供列表跳入新项目 §4.3) */
  readonly onCreated: (projectId: string) => void;
}

export function CreateProjectDialog(props: CreateProjectDialogProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const [name, setName] = useState('');
  const [key, setKey] = useState('');
  const [keyTouched, setKeyTouched] = useState(false);
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState<ProjectVisibility>('public');
  const [targetDate, setTargetDate] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const effectiveKey = keyTouched ? key : suggestProjectKey(name);
  const keyValid = isValidProjectKey(effectiveKey);
  const canSubmit = name.trim().length > 0 && name.trim().length <= 120 && keyValid && !isSubmitting;

  const handleNameChange = (value: string): void => {
    setName(value);
  };

  const handleKeyChange = (value: string): void => {
    setKeyTouched(true);
    setKey(value.toUpperCase());
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!canSubmit) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createProject(props.client, props.workspaceId, {
        name: name.trim(),
        key: effectiveKey,
        description: description.trim() === '' ? undefined : description.trim(),
        visibility,
        target_date: targetDate === '' ? undefined : targetDate,
      });
      toast.addToast(t('projects.create.success'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      props.onCreated(created.id);
      props.onClose();
    } catch (err) {
      const errorKey =
        err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      setSubmitError(t(errorKey));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      title={t('projects.create.title')}
      closeLabel={t('common.close')}
    >
      <form className="mesh-projects__form" onSubmit={handleSubmit} data-testid="create-project-form">
        <Input
          label={t('projects.create.name')}
          value={name}
          data-testid="create-project-name"
          onChange={(event) => handleNameChange(event.target.value)}
        />
        <Input
          label={t('projects.create.key')}
          value={effectiveKey}
          data-testid="create-project-key"
          error={effectiveKey.length > 0 && !keyValid ? t('projects.create.keyInvalid') : undefined}
          hint={keyValid ? t('projects.create.keyValid') : undefined}
          onChange={(event) => handleKeyChange(event.target.value)}
        />
        <LabeledTextarea
          label={t('projects.create.description')}
          value={description}
          onChange={setDescription}
        />
        <Select
          label={t('projects.create.visibility')}
          value={visibility}
          data-testid="create-project-visibility"
          onChange={(event) => setVisibility(event.target.value as ProjectVisibility)}
        >
          <option value="public">{t('projects.visibility.public')}</option>
          <option value="private">{t('projects.visibility.private')}</option>
        </Select>
        <Input
          type="date"
          label={t('projects.create.targetDate')}
          value={targetDate}
          data-testid="create-project-target-date"
          onChange={(event) => setTargetDate(event.target.value)}
        />
        {submitError !== null ? (
          <p className="mesh-field__error" role="alert" data-testid="create-project-error">
            {submitError}
          </p>
        ) : null}
        <div className="mesh-projects__form-actions">
          <Button type="button" variant="secondary" onClick={props.onClose}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" variant="primary" disabled={!canSubmit} data-testid="create-project-submit">
            {t('projects.create.submit')}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
