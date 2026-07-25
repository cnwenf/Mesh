/**
 * 成员与角色节区(workspace.md §4「RBAC 角色呈现」/ member.md §4)。
 *
 * - 角色矩阵:owner/admin/member/guest × 能力列(消费后端 RBAC 裁决构件呈现);
 * - 名册区:经 member.md §3 契约的 listMembers 消费;端点随 member 全量增量(MES-14)
 *   就绪,此前 404/405 优雅降级(提示而非错误态);
 * - 行内角色变更:PATCH(v0.4.0 已就绪),last_owner / agent_owner_not_allowed 具名呈现,
 *   agent 行禁用 owner 选项(后端同样强校验,member.md §2.2)。
 * - realtime:member.added / member.role_changed 触发名册刷新(§4.5)。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../api/client';
import { MeshApiError, errorToI18nKey } from '../api/errors';
import { getApiClient } from '../api/instance';
import { listMembers, updateMemberRole } from '../api/members';
import type { MemberSummary } from '../api/members';
import type { WorkspaceRole } from '../api/workspace';
import { Select, useToast } from '../design';
import { useT } from '../i18n';
import { useRealtimeContext } from '../shell/AppShell';
import { workspaceChannel } from './WorkspaceProvider';

const MATRIX_ROLES: readonly WorkspaceRole[] = ['owner', 'admin', 'member', 'guest'];
const MATRIX_CAPABILITIES = ['settings', 'invitations', 'members', 'delete'] as const;
type Capability = (typeof MATRIX_CAPABILITIES)[number];

/** 角色 × 能力矩阵(与 auth.md RBAC / workspace.md §3.1 最低角色一致) */
const CAPABILITY_MATRIX: Record<Capability, ReadonlySet<WorkspaceRole>> = {
  settings: new Set(['owner', 'admin']),
  invitations: new Set(['owner', 'admin']),
  members: new Set(['owner', 'admin']),
  delete: new Set(['owner']),
};

/** 邀请可设角色全集(含 owner 用于矩阵;行内变更选项不含 owner 给 agent) */
const ALL_ROLES: readonly WorkspaceRole[] = ['owner', 'admin', 'member', 'guest'];

export interface RolesMatrixProps {
  workspaceId: string;
  client?: MeshApiClient;
}

export function RolesMatrix(props: RolesMatrixProps): React.JSX.Element {
  const { workspaceId } = props;
  const client = props.client ?? getApiClient();
  const t = useT();
  const { addToast } = useToast();
  const realtime = useRealtimeContext();

  const [roster, setRoster] = useState<readonly MemberSummary[] | null>(null);
  const [rosterUnavailable, setRosterUnavailable] = useState(false);
  const [changingId, setChangingId] = useState<string | null>(null);
  const [changeError, setChangeError] = useState<{ key: string; memberId: string } | null>(null);

  const loadRoster = useCallback(async (): Promise<void> => {
    try {
      const page = await listMembers(client, workspaceId);
      setRoster(page.data);
      setRosterUnavailable(false);
    } catch (err) {
      // 名册端点随 member 全量增量(MES-14)提供;此前优雅降级(非错误态)。
      if (err instanceof MeshApiError && (err.status === 404 || err.status === 405)) {
        setRoster(null);
        setRosterUnavailable(true);
        return;
      }
      setRoster(null);
      setRosterUnavailable(true);
    }
  }, [client, workspaceId]);

  useEffect(() => {
    void loadRoster();
  }, [loadRoster]);

  // realtime:名册变更触发刷新(member.added / member.role_changed,§4.5)。
  useEffect(() => {
    if (realtime === null) return;
    const channel = workspaceChannel(workspaceId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      if (frame.event === 'member.added' || frame.event === 'member.role_changed') {
        void loadRoster();
      }
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspaceId, loadRoster]);

  const changeRole = async (member: MemberSummary, role: WorkspaceRole): Promise<void> => {
    setChangingId(member.id);
    setChangeError(null);
    try {
      const updated = await updateMemberRole(client, workspaceId, member.id, role);
      setRoster((prev) =>
        prev === null ? prev : prev.map((item) => (item.id === updated.id ? updated : item)),
      );
      addToast(t('roles.changedToast'), { tone: 'success', closeLabel: t('a11y.dismiss') });
    } catch (err) {
      setChangeError({
        key: err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown',
        memberId: member.id,
      });
    } finally {
      setChangingId(null);
    }
  };

  return (
    <div className="mesh-roles" data-testid="roles-section">
      <table data-testid="roles-matrix">
        <caption>{t('roles.matrixCaption')}</caption>
        <thead>
          <tr>
            <th>{t('roles.matrixCapability')}</th>
            {MATRIX_ROLES.map((role) => (
              <th key={role}>{t(`roles.${role}`)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {MATRIX_CAPABILITIES.map((capability) => (
            <tr key={capability}>
              <td>{t(`roles.capability.${capability}`)}</td>
              {MATRIX_ROLES.map((role) => (
                <td key={role} aria-label={t(`roles.${role}`)}>
                  {CAPABILITY_MATRIX[capability].has(role) ? t('roles.yes') : t('roles.no')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <h4>{t('roles.rosterTitle')}</h4>
      {rosterUnavailable ? (
        <p data-testid="roles-roster-unavailable">{t('roles.rosterUnavailable')}</p>
      ) : null}
      {roster !== null && roster.length === 0 ? (
        <p data-testid="roles-roster-empty">{t('roles.rosterEmpty')}</p>
      ) : null}
      {roster !== null && roster.length > 0 ? (
        <table data-testid="roles-roster">
          <tbody>
            {roster.map((member) => (
              <tr key={member.id} data-testid="roles-roster-row">
                <td>{member.display_name ?? member.id}</td>
                <td>{t(`roles.type.${member.member_type}`)}</td>
                <td>
                  <Select
                    label={t('roles.roleLabel', {
                      name: member.display_name ?? member.id,
                    })}
                    data-testid="roles-roster-select"
                    value={member.role}
                    disabled={changingId === member.id}
                    onChange={(event) =>
                      void changeRole(member, event.target.value as WorkspaceRole)
                    }
                  >
                    {ALL_ROLES.filter(
                      (role) => !(member.member_type === 'agent' && role === 'owner'),
                    ).map((role) => (
                      <option key={role} value={role}>
                        {t(`roles.${role}`)}
                      </option>
                    ))}
                  </Select>
                  {changeError !== null && changeError.memberId === member.id ? (
                    <p role="alert" data-testid="roles-change-error">
                      {t(changeError.key)}
                    </p>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
