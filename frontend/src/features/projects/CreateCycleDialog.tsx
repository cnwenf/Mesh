/**
 * 新建周期对话框(§1.2.5):名称 + 起止日期 + auto_roll;客户端校验 ends_at >= starts_at
 * (服务端 400 的镜像,§5.1),避免无效往返。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { MeshApiError, errorToI18nKey } from '../../api';
import type { MeshApiClient } from '../../api';
import { Button, Dialog, Input, useToast } from '../../design';
import { useT } from '../../i18n';
import { createCycle } from './api';
import type { Cycle } from './types';

export interface CreateCycleDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly onCreated: (cycle: Cycle) => void;
}

export function CreateCycleDialog(props: CreateCycleDialogProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const [name, setName] = useState('');
  const [startsAt, setStartsAt] = useState('');
  const [endsAt, setEndsAt] = useState('');
  const [autoRoll, setAutoRoll] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const rangeInvalid = startsAt !== '' && endsAt !== '' && endsAt < startsAt;
  const canSubmit =
    name.trim().length > 0 && startsAt !== '' && endsAt !== '' && !rangeInvalid && !isSubmitting;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!canSubmit) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createCycle(props.client, props.workspaceId, {
        name: name.trim(),
        starts_at: startsAt,
        ends_at: endsAt,
        auto_roll: autoRoll,
      });
      toast.addToast(t('cycles.create.success'), { tone: 'success', closeLabel: t('common.close') });
      props.onCreated(created);
      props.onClose();
    } catch (err) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      setSubmitError(t(key));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      title={t('cycles.create.title')}
      closeLabel={t('common.close')}
    >
      <form className="mesh-projects__form" onSubmit={handleSubmit} data-testid="create-cycle-form">
        <Input
          label={t('cycles.create.name')}
          value={name}
          data-testid="cycle-name-input"
          onChange={(event) => setName(event.target.value)}
        />
        <Input
          type="date"
          label={t('cycles.create.startsAt')}
          value={startsAt}
          data-testid="cycle-starts-input"
          onChange={(event) => setStartsAt(event.target.value)}
        />
        <Input
          type="date"
          label={t('cycles.create.endsAt')}
          value={endsAt}
          data-testid="cycle-ends-input"
          error={rangeInvalid ? t('cycles.create.rangeError') : undefined}
          onChange={(event) => setEndsAt(event.target.value)}
        />
        <label className="mesh-projects__check">
          <input
            type="checkbox"
            checked={autoRoll}
            data-testid="cycle-auto-roll"
            onChange={(event) => setAutoRoll(event.target.checked)}
          />
          {t('cycles.create.autoRoll')}
        </label>
        {submitError !== null ? (
          <p className="mesh-field__error" role="alert" data-testid="create-cycle-error">
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
            data-testid="create-cycle-submit"
          >
            {t('cycles.create.submit')}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
