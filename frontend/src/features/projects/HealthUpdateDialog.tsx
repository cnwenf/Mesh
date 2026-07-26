/**
 * 「更新状态」对话框(§4.2/§4.3):选健康度 + 写说明 → 提交留痕(addProjectUpdate,
 * 服务端同时回写 projects.health/status 并广播 project_update.added + project.updated)。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { MeshApiError, errorToI18nKey } from '../../api';
import type { MeshApiClient } from '../../api';
import { Button, Dialog, Select, useToast } from '../../design';
import { useT } from '../../i18n';
import { addProjectUpdate } from './api';
import { LabeledTextarea } from './widgets';
import type { ProjectHealth } from './types';
import { PROJECT_HEALTH_ORDER } from './types';

export interface HealthUpdateDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly client: MeshApiClient;
  readonly projectId: string;
  /** 留痕成功后的回调(重载头部/动态) */
  readonly onSaved: () => void;
}

export function HealthUpdateDialog(props: HealthUpdateDialogProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const [health, setHealth] = useState<ProjectHealth>('on_track');
  const [message, setMessage] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (isSubmitting) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      await addProjectUpdate(props.client, props.projectId, {
        health,
        message: message.trim() === '' ? undefined : message.trim(),
      });
      toast.addToast(t('projects.health.success'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      props.onSaved();
      props.onClose();
    } catch (err) {
      const errorKey = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      setSubmitError(t(errorKey));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      title={t('projects.health.dialogTitle')}
      closeLabel={t('common.close')}
    >
      <form className="mesh-projects__form" onSubmit={handleSubmit} data-testid="health-update-form">
        <Select
          label={t('projects.health.label')}
          value={health}
          data-testid="health-select"
          onChange={(event) => setHealth(event.target.value as ProjectHealth)}
        >
          {PROJECT_HEALTH_ORDER.map((value) => (
            <option key={value} value={value}>
              {t(`projects.health.${value}`)}
            </option>
          ))}
        </Select>
        <LabeledTextarea
          label={t('projects.health.message')}
          value={message}
          onChange={setMessage}
        />
        {submitError !== null ? (
          <p className="mesh-field__error" role="alert" data-testid="health-update-error">
            {submitError}
          </p>
        ) : null}
        <div className="mesh-projects__form-actions">
          <Button type="button" variant="secondary" onClick={props.onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={isSubmitting}
            data-testid="health-update-submit"
          >
            {t('projects.health.submit')}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
