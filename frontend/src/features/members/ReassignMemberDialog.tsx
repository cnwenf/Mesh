/**
 * 成员名下任务批量转派对话框(L247,member.md「reassign」:POST /members/reassign):
 * 把源成员名下未完成 issue 整体转给另一名活跃成员;成功后回报转派条数。
 */
import { useState } from 'react';
import { Button, Dialog, Select, useToast } from '../../design';
import { useT } from '../../i18n';
import type { MeshApiClient } from '../../api';
import { reassignIssues } from './api';
import type { MemberSummary } from './types';

interface ReassignMemberDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  /** 名下任务被转派的源成员。 */
  readonly member: MemberSummary;
  /** 可作为目标的其他活跃成员。 */
  readonly targets: readonly MemberSummary[];
}

export function ReassignMemberDialog(props: ReassignMemberDialogProps): React.JSX.Element {
  const { open, onClose, client, workspaceId, member, targets } = props;
  const t = useT();
  const toast = useToast();
  const [targetId, setTargetId] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = async (): Promise<void> => {
    // 确认按钮在 targetId 为空时 disabled,此处不会出现空目标提交。
    setIsSubmitting(true);
    setError(null);
    try {
      const result = await reassignIssues(client, workspaceId, member.id, targetId);
      toast.addToast(t('members.reassign.result', { count: result.reassigned_issues }), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common.unknownError'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={t('members.reassign.title')} closeLabel={t('common.close')}>
      <div className="mesh-members__dialog-body">
        <p data-testid="reassign-dialog-body">
          {t('members.reassign.confirm', { name: member.display_name })}
        </p>
        <Select
          label={t('members.reassign.targetLabel')}
          value={targetId}
          data-testid="reassign-dialog-target"
          onChange={(event) => setTargetId(event.target.value)}
        >
          <option value="">{t('members.reassign.targetPlaceholder')}</option>
          {targets.map((target) => (
            <option key={target.id} value={target.id}>
              {target.display_name}
            </option>
          ))}
        </Select>
        {error !== null ? <p className="mesh-members__error">{error}</p> : null}
        <div className="mesh-members__dialog-footer">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => void handleConfirm()}
            isLoading={isSubmitting}
            disabled={targetId === ''}
            data-testid="reassign-dialog-confirm"
          >
            {t('members.reassign.submit')}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
