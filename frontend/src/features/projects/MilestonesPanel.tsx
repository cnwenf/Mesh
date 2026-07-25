/**
 * 里程碑面板(§4.1 里程碑 Tab):列表(逾期标红)+ 状态切换(open↔closed,updateMilestone)+
 * 删除(二次确认)+ 新建对话框。数据由详情页持有(实时 milestone.* 帧在详情页合并)。
 */
import { useState } from 'react';
import { MeshApiError, errorToI18nKey } from '../../api';
import type { MeshApiClient } from '../../api';
import { Button, Dialog, EmptyState, useToast } from '../../design';
import { useT } from '../../i18n';
import { deleteMilestone, updateMilestone } from './api';
import { CreateMilestoneDialog } from './CreateMilestoneDialog';
import type { Milestone } from './types';

export interface MilestonesPanelProps {
  readonly client: MeshApiClient;
  readonly projectId: string;
  readonly milestones: readonly Milestone[];
  readonly upsertMilestone: (milestone: Milestone) => void;
  readonly removeMilestone: (milestoneId: string) => void;
}

export function MilestonesPanel(props: MilestonesPanelProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const [createOpen, setCreateOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<Milestone | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const reportError = (err: unknown): void => {
    const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
    toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
  };

  const handleToggle = async (milestone: Milestone): Promise<void> => {
    const nextState = milestone.state === 'open' ? 'closed' : 'open';
    try {
      const updated = await updateMilestone(props.client, milestone.id, { state: nextState });
      props.upsertMilestone(updated);
    } catch (err) {
      reportError(err);
    }
  };

  const handleDeleteConfirmed = async (): Promise<void> => {
    if (confirmDelete === null) return;
    setIsDeleting(true);
    try {
      await deleteMilestone(props.client, confirmDelete.id);
      props.removeMilestone(confirmDelete.id);
      setConfirmDelete(null);
    } catch (err) {
      reportError(err);
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <section className="mesh-projects__panel" aria-label={t('projects.tab.milestones')}>
      <div className="mesh-projects__panel-head">
        <Button
          variant="secondary"
          data-testid="create-milestone-button"
          onClick={() => setCreateOpen(true)}
        >
          {t('projects.milestones.create')}
        </Button>
      </div>
      {props.milestones.length === 0 ? (
        <EmptyState title={t('state.emptyTitle')} description={t('projects.milestones.empty')} />
      ) : (
        <ul className="mesh-projects__milestone-list" data-testid="milestone-list">
          {props.milestones.map((milestone) => (
            <li
              key={milestone.id}
              className={
                milestone.overdue
                  ? 'mesh-projects__milestone mesh-projects__milestone--overdue'
                  : 'mesh-projects__milestone'
              }
              data-testid={`milestone-${milestone.id}`}
            >
              <div className="mesh-projects__milestone-info">
                <span className="mesh-projects__milestone-title">{milestone.title}</span>
                <span className="mesh-projects__milestone-sub">
                  {t(`projects.milestones.state.${milestone.state}`)}
                  {milestone.target_date !== null
                    ? ` · ${t('projects.card.due', { date: milestone.target_date })}`
                    : ''}
                  {milestone.overdue ? ` · ${t('projects.milestones.overdue')}` : ''}
                </span>
              </div>
              <div className="mesh-projects__milestone-actions">
                <Button
                  size="sm"
                  variant="ghost"
                  data-testid={`milestone-toggle-${milestone.id}`}
                  onClick={() => void handleToggle(milestone)}
                >
                  {milestone.state === 'open'
                    ? t('projects.milestones.markClosed')
                    : t('projects.milestones.markOpen')}
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  data-testid={`milestone-delete-${milestone.id}`}
                  onClick={() => setConfirmDelete(milestone)}
                >
                  {t('projects.milestones.delete')}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <CreateMilestoneDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        client={props.client}
        projectId={props.projectId}
        onCreated={props.upsertMilestone}
      />

      {confirmDelete !== null ? (
        <Dialog
          open
          onClose={() => setConfirmDelete(null)}
          title={t('projects.milestones.deleteTitle')}
          closeLabel={t('common.close')}
        >
          <p data-testid="milestone-delete-confirm-text">
            {t('projects.milestones.deleteConfirm', { title: confirmDelete.title })}
          </p>
          <div className="mesh-projects__form-actions">
            <Button variant="secondary" onClick={() => setConfirmDelete(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              disabled={isDeleting}
              data-testid="milestone-delete-confirm"
              onClick={() => void handleDeleteConfirmed()}
            >
              {t('projects.milestones.deleteSubmit')}
            </Button>
          </div>
        </Dialog>
      ) : null}
    </section>
  );
}
