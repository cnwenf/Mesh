/**
 * 更新动态面板(§4.1 更新动态 Tab):留痕时间线(作者 / 健康度 / 消息 / 时间)+
 * 内联提交表单(健康度 + 说明 → addProjectUpdate;服务端回写 projects.health/status)。
 */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { MeshApiError, errorToI18nKey } from '../../api';
import type { MeshApiClient } from '../../api';
import { Button, EmptyState, Select, useToast } from '../../design';
import { useT } from '../../i18n';
import { addProjectUpdate } from './api';
import { LabeledTextarea } from './widgets';
import type { ProjectHealth, ProjectUpdateEntry } from './types';
import { PROJECT_HEALTH_ORDER } from './types';

export interface UpdatesPanelProps {
  readonly client: MeshApiClient;
  readonly projectId: string;
  readonly updates: readonly ProjectUpdateEntry[];
  readonly prependUpdate: (update: ProjectUpdateEntry) => void;
  /** 留痕成功后回调(头部健康度/状态已回写,需重载) */
  readonly onSubmitted: () => void;
}

export function UpdatesPanel(props: UpdatesPanelProps): React.JSX.Element {
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
      const created = await addProjectUpdate(props.client, props.projectId, {
        health,
        message: message.trim() === '' ? undefined : message.trim(),
      });
      props.prependUpdate(created);
      props.onSubmitted();
      setMessage('');
      toast.addToast(t('projects.updates.success'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch (err) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      setSubmitError(t(key));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="mesh-projects__panel" aria-label={t('projects.tab.updates')}>
      <form className="mesh-projects__form mesh-projects__update-form" onSubmit={handleSubmit}>
        <Select
          label={t('projects.health.label')}
          value={health}
          data-testid="update-health-select"
          onChange={(event) => setHealth(event.target.value as ProjectHealth)}
        >
          {PROJECT_HEALTH_ORDER.map((value) => (
            <option key={value} value={value}>
              {t(`projects.health.${value}`)}
            </option>
          ))}
        </Select>
        <LabeledTextarea
          label={t('projects.updates.messageLabel')}
          value={message}
          onChange={setMessage}
        />
        {submitError !== null ? (
          <p className="mesh-field__error" role="alert" data-testid="update-submit-error">
            {submitError}
          </p>
        ) : null}
        <div className="mesh-projects__form-actions">
          <Button type="submit" variant="primary" disabled={isSubmitting} data-testid="update-submit">
            {t('projects.updates.submit')}
          </Button>
        </div>
      </form>

      {props.updates.length === 0 ? (
        <EmptyState title={t('state.emptyTitle')} description={t('projects.updates.empty')} />
      ) : (
        <ul className="mesh-projects__update-list" data-testid="update-list">
          {props.updates.map((update) => (
            <li key={update.id} className="mesh-projects__update" data-testid={`update-${update.id}`}>
              <div className="mesh-projects__update-head">
                <span className="mesh-projects__update-author">
                  {update.author !== null ? update.author.name : t('projects.updates.unknownAuthor')}
                </span>
                {update.health !== null ? (
                  <span className={`mesh-projects__health-chip mesh-projects__health-chip--${update.health}`}>
                    {t(`projects.health.${update.health}`)}
                  </span>
                ) : null}
                <time className="mesh-projects__update-time" dateTime={update.created_at}>
                  {update.created_at}
                </time>
              </div>
              {update.message !== null ? (
                <p className="mesh-projects__update-message">{update.message}</p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
