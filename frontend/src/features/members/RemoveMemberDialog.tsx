/**
 * 停用 / 移除成员确认弹窗(member.md §4.2/§4.3):二次确认,移除时提供「转派目标」选择器
 * (把其名下未完成 issue 转派给另一名活跃成员;issue 模块落地前转派数为 0,见 reassign.ts)。
 */
import { useState } from 'react';
import { Button, Dialog, Select } from '../../design';
import { useT } from '../../i18n';
import type { MeshApiClient } from '../../api';
import { removeMember, updateMember } from './api';
import type { MemberSummary } from './types';

export type RemoveMode = 'disable' | 'remove';

interface RemoveMemberDialogProps {
  readonly open: boolean;
  readonly mode: RemoveMode;
  readonly onClose: () => void;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly member: MemberSummary;
  /** 可作为转派目标的其他活跃成员。 */
  readonly reassignTargets: readonly MemberSummary[];
  readonly onChanged: () => void;
}

export function RemoveMemberDialog(props: RemoveMemberDialogProps): React.JSX.Element {
  const { open, mode, onClose, client, workspaceId, member, reassignTargets, onChanged } = props;
  const t = useT();
  const [reassignTo, setReassignTo] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isRemove = mode === 'remove';
  const title = isRemove ? t('members.remove.title') : t('members.disable.title');

  const handleConfirm = async (): Promise<void> => {
    setIsSubmitting(true);
    setError(null);
    try {
      if (isRemove) {
        await removeMember(client, workspaceId, member.id, reassignTo || undefined);
      } else {
        await updateMember(client, workspaceId, member.id, { status: 'disabled' });
      }
      onChanged();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common.unknownError'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title={title} closeLabel={t('common.close')}>
      <div className="mesh-members__dialog-body">
        <p>
          {isRemove
            ? t('members.remove.confirm', { name: member.display_name })
            : t('members.disable.confirm', { name: member.display_name })}
        </p>
        {isRemove ? (
          <Select
            label={t('members.remove.reassignLabel')}
            value={reassignTo}
            data-testid="reassign-target"
            onChange={(event) => setReassignTo(event.target.value)}
          >
            <option value="">{t('members.remove.reassignNone')}</option>
            {reassignTargets.map((target) => (
              <option key={target.id} value={target.id}>
                {target.display_name}
              </option>
            ))}
          </Select>
        ) : null}
        {error ? <p className="mesh-members__error">{error}</p> : null}
        <div className="mesh-members__dialog-footer">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant={isRemove ? 'danger' : 'primary'}
            onClick={handleConfirm}
            isLoading={isSubmitting}
            data-testid="remove-confirm"
          >
            {isRemove ? t('members.remove.submit') : t('members.disable.submit')}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
