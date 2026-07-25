/**
 * 新建里程碑对话框(§4.3):标题 + 目标日 → createMilestone。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { MeshApiError, errorToI18nKey } from '../../api';
import type { MeshApiClient } from '../../api';
import { Button, Dialog, Input, useToast } from '../../design';
import { useT } from '../../i18n';
import { createMilestone } from './api';
import type { Milestone } from './types';

export interface CreateMilestoneDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly client: MeshApiClient;
  readonly projectId: string;
  readonly onCreated: (milestone: Milestone) => void;
}

export function CreateMilestoneDialog(props: CreateMilestoneDialogProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const [title, setTitle] = useState('');
  const [targetDate, setTargetDate] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const canSubmit = title.trim().length > 0 && !isSubmitting;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!canSubmit) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createMilestone(props.client, props.projectId, {
        title: title.trim(),
        target_date: targetDate === '' ? undefined : targetDate,
      });
      toast.addToast(t('projects.milestones.createSuccess'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      props.onCreated(created);
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
      title={t('projects.milestones.createTitle')}
      closeLabel={t('common.close')}
    >
      <form className="mesh-projects__form" onSubmit={handleSubmit} data-testid="create-milestone-form">
        <Input
          label={t('projects.milestones.titleLabel')}
          value={title}
          data-testid="milestone-title-input"
          onChange={(event) => setTitle(event.target.value)}
        />
        <Input
          type="date"
          label={t('projects.milestones.targetDateLabel')}
          value={targetDate}
          data-testid="milestone-target-input"
          onChange={(event) => setTargetDate(event.target.value)}
        />
        {submitError !== null ? (
          <p className="mesh-field__error" role="alert" data-testid="create-milestone-error">
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
            disabled={!canSubmit}
            data-testid="create-milestone-submit"
          >
            {t('projects.milestones.createSubmit')}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
