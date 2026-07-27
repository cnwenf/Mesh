/**
 * 邀请人类成员弹窗(member.md §4.2):邮箱 → workspace.md 邀请。
 *
 * Agent 创建不走本弹窗:唯一入口是名册页「+ 新建 Agent」打开的 AgentWizard
 * (README §6.12 / agent.md §4.2,T35 —— 不存在第二创建入口)。
 */
import { useState } from 'react';
import { Button, Dialog, Input, Select } from '../../design';
import { useT } from '../../i18n';
import type { MeshApiClient } from '../../api';
import { createInvitation } from './api';
import type { MemberRole } from './types';

interface AddMemberDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
  readonly client: MeshApiClient;
  readonly workspaceId: string;
  readonly onInvited: () => void;
}

const INVITE_ROLES: readonly MemberRole[] = ['admin', 'member', 'guest'];

export function AddMemberDialog(props: AddMemberDialogProps): React.JSX.Element {
  const { open, onClose, client, workspaceId, onInvited } = props;
  const t = useT();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<MemberRole>('member');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const handleInvite = async (): Promise<void> => {
    setIsSubmitting(true);
    setError(null);
    try {
      await createInvitation(client, workspaceId, email.trim(), role);
      setDone(true);
      setEmail('');
      onInvited();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common.unknownError'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t('members.add.title')}
      closeLabel={t('common.close')}
    >
      <div className="mesh-members__dialog-body">
        <Input
          label={t('members.add.emailLabel')}
          type="email"
          value={email}
          data-testid="invite-email"
          onChange={(event) => setEmail(event.target.value)}
        />
        <Select
          label={t('members.add.roleLabel')}
          value={role}
          data-testid="invite-role"
          onChange={(event) => setRole(event.target.value as MemberRole)}
        >
          {INVITE_ROLES.map((r) => (
            <option key={r} value={r}>
              {t(`members.role.${r}`)}
            </option>
          ))}
        </Select>
        {done ? <p data-testid="invite-done">{t('members.add.invited')}</p> : null}
        {error ? <p className="mesh-members__error">{error}</p> : null}
        <div className="mesh-members__dialog-footer">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleInvite}
            isLoading={isSubmitting}
            disabled={email.trim() === ''}
            data-testid="invite-submit"
          >
            {t('members.add.inviteSubmit')}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
