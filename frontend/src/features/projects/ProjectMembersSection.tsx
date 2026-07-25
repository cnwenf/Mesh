/**
 * 项目成员设置区(§2.2 project_members):成员列表 + 角色切换(lead/member/viewer)+
 * 移除 + 从工作区名册添加(active 且未入项者;409 project_member_exists 就地 toast)。
 */
import { useCallback, useEffect, useState } from 'react';
import { MeshApiError, errorToI18nKey } from '../../api';
import type { MeshApiClient } from '../../api';
import { Button, EmptyState, Select, Skeleton, useToast } from '../../design';
import { useT } from '../../i18n';
import type { MemberSummary } from '../members/types';
import { addProjectMember, listProjectMembers, removeProjectMember, updateProjectMemberRole } from './api';
import type { ProjectMemberEntry, ProjectMemberRole } from './types';
import { PROJECT_MEMBER_ROLE_ORDER } from './types';

export interface ProjectMembersSectionProps {
  readonly client: MeshApiClient;
  readonly projectId: string;
  /** 工作区名册(添加成员的候选来源) */
  readonly roster: readonly MemberSummary[];
}

export function ProjectMembersSection(props: ProjectMembersSectionProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const [members, setMembers] = useState<ProjectMemberEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [addMemberId, setAddMemberId] = useState('');
  const [addRole, setAddRole] = useState<ProjectMemberRole>('member');

  const reportError = useCallback(
    (err: unknown): void => {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    },
    [toast, t],
  );

  const load = useCallback(() => {
    setIsLoading(true);
    listProjectMembers(props.client, props.projectId, { limit: 100 })
      .then((page) => setMembers([...page.data]))
      .catch(reportError)
      .finally(() => setIsLoading(false));
  }, [props.client, props.projectId, reportError]);

  useEffect(() => {
    load();
  }, [load]);

  const handleRoleChange = async (entry: ProjectMemberEntry, role: ProjectMemberRole): Promise<void> => {
    try {
      const updated = await updateProjectMemberRole(props.client, props.projectId, entry.member_id, {
        role,
      });
      setMembers((prev) => prev.map((m) => (m.member_id === updated.member_id ? updated : m)));
      toast.addToast(t('projects.settings.members.roleChanged'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch (err) {
      reportError(err);
    }
  };

  const handleRemove = async (entry: ProjectMemberEntry): Promise<void> => {
    try {
      await removeProjectMember(props.client, props.projectId, entry.member_id);
      setMembers((prev) => prev.filter((m) => m.member_id !== entry.member_id));
      toast.addToast(t('projects.settings.members.removed'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch (err) {
      reportError(err);
    }
  };

  const handleAdd = async (): Promise<void> => {
    if (addMemberId === '') return;
    try {
      const created = await addProjectMember(props.client, props.projectId, {
        member_id: addMemberId,
        role: addRole,
      });
      setMembers((prev) => [...prev, created]);
      setAddMemberId('');
      toast.addToast(t('projects.settings.members.added'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch (err) {
      reportError(err);
    }
  };

  const memberIds = new Set(members.map((m) => m.member_id));
  const candidates = props.roster.filter(
    (member) => member.status === 'active' && !memberIds.has(member.id),
  );

  return (
    <section className="mesh-projects__settings-section" aria-label={t('projects.settings.members.title')}>
      <h2 className="mesh-projects__settings-subtitle">{t('projects.settings.members.title')}</h2>
      {isLoading ? (
        <Skeleton loadingLabel={t('common.loading')} />
      ) : members.length === 0 ? (
        <EmptyState title={t('state.emptyTitle')} description={t('projects.settings.members.empty')} />
      ) : (
        <ul className="mesh-projects__member-list" data-testid="project-member-list">
          {members.map((entry) => (
            <li key={entry.member_id} className="mesh-projects__member-row" data-testid={`member-row-${entry.member_id}`}>
              <span className="mesh-projects__member-name">
                {entry.member !== null ? entry.member.name : entry.member_id}
              </span>
              <Select
                label={t('projects.settings.members.roleLabel')}
                value={entry.role}
                data-testid={`member-role-${entry.member_id}`}
                onChange={(event) =>
                  void handleRoleChange(entry, event.target.value as ProjectMemberRole)
                }
              >
                {PROJECT_MEMBER_ROLE_ORDER.map((role) => (
                  <option key={role} value={role}>
                    {t(`projects.role.${role}`)}
                  </option>
                ))}
              </Select>
              <Button
                size="sm"
                variant="danger"
                data-testid={`member-remove-${entry.member_id}`}
                onClick={() => void handleRemove(entry)}
              >
                {t('projects.settings.members.remove')}
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="mesh-projects__member-add" data-testid="member-add-form">
        <Select
          label={t('projects.settings.members.memberLabel')}
          value={addMemberId}
          data-testid="add-member-select"
          onChange={(event) => setAddMemberId(event.target.value)}
        >
          <option value="">{t('projects.settings.members.memberPlaceholder')}</option>
          {candidates.map((member) => (
            <option key={member.id} value={member.id}>
              {member.display_name}
            </option>
          ))}
        </Select>
        <Select
          label={t('projects.settings.members.roleLabel')}
          value={addRole}
          data-testid="add-member-role"
          onChange={(event) => setAddRole(event.target.value as ProjectMemberRole)}
        >
          {PROJECT_MEMBER_ROLE_ORDER.map((role) => (
            <option key={role} value={role}>
              {t(`projects.role.${role}`)}
            </option>
          ))}
        </Select>
        <Button
          variant="secondary"
          disabled={addMemberId === ''}
          data-testid="add-member-submit"
          onClick={() => void handleAdd()}
        >
          {t('projects.settings.members.add')}
        </Button>
      </div>
    </section>
  );
}
