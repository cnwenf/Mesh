/**
 * 成员名册页(member.md §4,README §6.12)。
 *
 * 唯一名册 + 唯一创建入口:人类与 agent 同表呈现,「仅 Agent」是同一路由
 * (`?member_type=agent`)的筛选投影 —— 同一列表组件、同一 `[ + 新建 Agent ]` 入口,
 * 不存在独立 Agents 列表页/第二导航/第二创建入口(T35)。
 *
 * 显示名由服务端按 §2.4 解析为单一 `display_name`;前端仅渲染,并据 `member_type`
 * 叠加「AI」徽章。角色/状态/显示名变更经 REST(乐观刷新后重拉名册)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import { Button, Dialog, EmptyState, ErrorState, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { AgentWizard } from '../agents/AgentWizard';
import { resetOnboardingMember } from '../onboarding/api';
import { EmptyRoster } from '../onboarding/illustrations';
import { activeWorkspace, fetchMe, getMember, listMembers, updateMember } from './api';
import { AddMemberDialog } from './AddMemberDialog';
import { RemoveMemberDialog } from './RemoveMemberDialog';
import type { RemoveMode } from './RemoveMemberDialog';
import type { MemberDetail, MemberRole, MemberSummary, MemberType, Membership } from './types';
import { ROLE_ORDER } from './types';
import './members.css';

const SEARCH_DEBOUNCE_MS = 300;

type TabKey = 'all' | 'human' | 'agent' | 'disabled';
type StatusFilter = 'default' | 'all' | 'active' | 'disabled' | 'removed';

const TAB_PARAMS: Record<TabKey, { memberType: 'all' | MemberType; status: StatusFilter }> = {
  all: { memberType: 'all', status: 'default' },
  human: { memberType: 'human', status: 'default' },
  agent: { memberType: 'agent', status: 'default' },
  disabled: { memberType: 'all', status: 'disabled' },
};

function tabFromParams(memberType: string | null, status: string | null): TabKey {
  if (status === 'disabled') return 'disabled';
  if (memberType === 'human') return 'human';
  if (memberType === 'agent') return 'agent';
  return 'all';
}

function memberSubtext(member: MemberSummary): string {
  const profile = member.profile;
  if (member.member_type === 'human' && profile && 'email' in profile) {
    return profile.email;
  }
  if (
    member.member_type === 'agent' &&
    profile &&
    'description' in profile &&
    profile.description
  ) {
    return profile.description;
  }
  return '';
}
export function MembersPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);

  const [searchParams, setSearchParams] = useSearchParams();
  const memberTypeParam = searchParams.get('member_type');
  const statusParam = searchParams.get('status');
  const activeTab = tabFromParams(memberTypeParam, statusParam);

  const [workspace, setWorkspace] = useState<Membership | null>(null);
  const [meId, setMeId] = useState<string | null>(null);
  const [members, setMembers] = useState<MemberSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [q, setQ] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

  const [addOpen, setAddOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [detail, setDetail] = useState<MemberDetail | null>(null);
  const [confirm, setConfirm] = useState<{ mode: RemoveMode; member: MemberSummary } | null>(null);
  /** 管理员重置上手进度的二次确认目标(onboarding.md §4.2;仅人类成员行) */
  const [resetTarget, setResetTarget] = useState<MemberSummary | null>(null);

  // Resolve the current workspace from the caller's memberships (single source
  // until the workspace picker lands with MES-24).
  useEffect(() => {
    let cancelled = false;
    fetchMe(client)
      .then((me) => {
        if (cancelled) return;
        setMeId(me.user.id);
        setWorkspace(activeWorkspace(me.memberships));
      })
      .catch(() => {
        if (!cancelled) setError(t('state.errorDescription'));
      });
    return () => {
      cancelled = true;
    };
  }, [client, t]);

  // Debounce the search box into the query term.
  useEffect(() => {
    const handle = setTimeout(() => setQ(searchInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [searchInput]);

  const loadRoster = useCallback(() => {
    if (workspace === null) {
      setIsLoading(false);
      return;
    }
    const { memberType, status } = TAB_PARAMS[activeTab];
    setIsLoading(true);
    setError(null);
    listMembers(client, workspace.workspace_id, {
      memberType,
      status,
      q: q || undefined,
      limit: 100,
    })
      .then((result) => setMembers(result.data))
      .catch((err) => setError(err instanceof Error ? err.message : t('state.errorDescription')))
      .finally(() => setIsLoading(false));
  }, [client, workspace, activeTab, q, t]);

  useEffect(() => {
    loadRoster();
  }, [loadRoster, reloadKey]);

  const selectTab = (tab: TabKey): void => {
    const params = new URLSearchParams();
    const { memberType, status } = TAB_PARAMS[tab];
    if (memberType !== 'all') params.set('member_type', memberType);
    if (status !== 'default') params.set('status', status);
    setSearchParams(params, { replace: true });
  };

  const handleRoleChange = async (member: MemberSummary, role: MemberRole): Promise<void> => {
    if (workspace === null) return;
    try {
      await updateMember(client, workspace.workspace_id, member.id, { role });
      toast.addToast(t('members.toast.roleUpdated'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setReloadKey((key) => key + 1);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const handleEnable = async (member: MemberSummary): Promise<void> => {
    if (workspace === null) return;
    try {
      await updateMember(client, workspace.workspace_id, member.id, { status: 'active' });
      setReloadKey((key) => key + 1);
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  // 管理员重置某人类成员的上手进度(onboarding.md §4.2):二次确认后调重置端点。
  const handleResetOnboarding = async (member: MemberSummary): Promise<void> => {
    if (workspace === null) return;
    setResetTarget(null);
    try {
      await resetOnboardingMember(client, workspace.workspace_id, member.id);
      toast.addToast(t('onboarding.reset.success'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
    } catch (err) {
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const openDetail = async (member: MemberSummary): Promise<void> => {
    // Agent 行深链到 agent 详情页(README §6.12 名册详情深链);人类行开抽屉。
    if (member.member_type === 'agent' && member.profile !== null && 'id' in member.profile) {
      navigate(`/agents/${member.profile.id}`);
      return;
    }
    if (workspace === null) return;
    try {
      const full = await getMember(client, workspace.workspace_id, member.id);
      setDetail(full);
    } catch {
      setDetail(null);
    }
  };

  const reassignTargets = useMemo(
    () =>
      members.filter((member) => member.status === 'active' && member.id !== confirm?.member.id),
    [members, confirm],
  );

  const canManage =
    workspace !== null && (workspace.role === 'owner' || workspace.role === 'admin');

  return (
    <main className="mesh-members">
      <div className="mesh-members__header">
        <h1 className="mesh-members__title">{t('members.title')}</h1>
        <div className="mesh-members__actions">
          {canManage ? (
            <>
              <Button
                variant="secondary"
                data-testid="invite-human-button"
                onClick={() => setAddOpen(true)}
              >
                {t('members.invite')}
              </Button>
              <Button
                variant="primary"
                data-testid="new-agent-button"
                onClick={() => setWizardOpen(true)}
              >
                {t('members.newAgent')}
              </Button>
            </>
          ) : null}
        </div>
      </div>

      <div className="mesh-members__toolbar">
        <div className="mesh-members__tabs" role="tablist" aria-label={t('members.filterLabel')}>
          {(['all', 'human', 'agent', 'disabled'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              className="mesh-members__tab"
              data-testid={`tab-${tab}`}
              onClick={() => selectTab(tab)}
            >
              {t(`members.tab.${tab}`)}
            </button>
          ))}
        </div>
        <input
          type="search"
          className="mesh-members__search"
          placeholder={t('common.search')}
          aria-label={t('common.search')}
          data-testid="member-search"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
        />
      </div>

      {workspace === null && !isLoading && error === null ? (
        <EmptyState title={t('state.emptyTitle')} description={t('members.noWorkspace')} />
      ) : error !== null ? (
        <ErrorState
          title={t('state.errorTitle')}
          description={error}
          retryLabel={t('common.retry')}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      ) : isLoading ? (
        <Skeleton loadingLabel={t('common.loading')} />
      ) : members.length === 0 ? (
        <EmptyState
          illustration={<EmptyRoster />}
          title={t('onboarding.empty.members.title')}
          description={t('onboarding.empty.members.description')}
          action={
            canManage ? (
              <div className="mesh-members__empty-actions">
                <Button
                  variant="secondary"
                  data-testid="members-empty-invite"
                  onClick={() => setAddOpen(true)}
                >
                  {t('onboarding.empty.members.action')}
                </Button>
                <Button
                  variant="primary"
                  data-testid="members-empty-agent"
                  onClick={() => setWizardOpen(true)}
                >
                  {t('onboarding.empty.members.actionAgent')}
                </Button>
              </div>
            ) : undefined
          }
        />
      ) : (
        <table className="mesh-members__table">
          <thead>
            <tr>
              <th scope="col">{t('members.col.name')}</th>
              <th scope="col">{t('agents.roster.type')}</th>
              <th scope="col">{t('members.col.contact')}</th>
              <th scope="col">{t('members.col.role')}</th>
              <th scope="col">{t('agents.roster.lifecycle')}</th>
              <th scope="col">{t('agents.roster.presence')}</th>
              <th scope="col">{t('members.col.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {members.map((member) => (
              <tr
                key={member.id}
                className={
                  member.status === 'removed'
                    ? 'mesh-members__row mesh-members__row--removed'
                    : 'mesh-members__row'
                }
              >
                <td>
                  <button
                    type="button"
                    className="mesh-members__identity"
                    data-testid={`member-open-${member.id}`}
                    onClick={() => openDetail(member)}
                    style={{
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      font: 'inherit',
                    }}
                  >
                    <span
                      className={
                        member.member_type === 'agent'
                          ? 'mesh-members__avatar mesh-members__avatar--agent'
                          : 'mesh-members__avatar'
                      }
                      aria-hidden="true"
                    >
                      {member.display_name.slice(0, 1).toUpperCase()}
                    </span>
                    <span className="mesh-members__name">{member.display_name}</span>
                    {member.member_type === 'agent' ? (
                      <span className="mesh-members__badge" data-testid={`ai-badge-${member.id}`}>
                        {t('members.badge.agent')}
                      </span>
                    ) : null}
                  </button>
                </td>
                <td className="mesh-members__sub" data-testid={`member-type-${member.id}`}>
                  {member.member_type === 'agent'
                    ? t('agents.roster.typeAgent')
                    : t('agents.roster.typeHuman')}
                </td>
                <td className="mesh-members__sub">{memberSubtext(member)}</td>
                <td>
                  {member.member_type === 'agent' ? (
                    // H-F1:agent 行展示 role_tag(§4.2/§4.5),而非工作区角色下拉。
                    <span data-testid={`member-role-tag-${member.id}`}>
                      {member.profile && 'role_tag' in member.profile
                        ? (member.profile as { role_tag?: string | null }).role_tag ?? ''
                        : ''}
                    </span>
                  ) : (
                    <select
                      className="mesh-members__role-select"
                      aria-label={t('members.col.role')}
                      data-testid={`role-select-${member.id}`}
                      value={member.role}
                      disabled={!canManage}
                      onChange={(event) =>
                        handleRoleChange(member, event.target.value as MemberRole)
                      }
                    >
                      {ROLE_ORDER.map((role) => (
                        <option key={role} value={role}>
                          {t(`members.role.${role}`)}
                        </option>
                      ))}
                    </select>
                  )}
                </td>
                <td className="mesh-members__sub" data-testid={`member-lifecycle-${member.id}`}>
                  {member.member_type === 'agent' &&
                  member.profile &&
                  'lifecycle_status' in member.profile &&
                  (member.profile as { lifecycle_status?: string | null }).lifecycle_status
                    ? t(
                        `agents.lifecycle.${(member.profile as { lifecycle_status: string }).lifecycle_status}`,
                      )
                    : t(`members.status.${member.status}`)}
                </td>
                <td className="mesh-members__sub" data-testid={`member-presence-${member.id}`}>
                  {/* §4.9 容量三元组脚手架:presence 帧由 runtime 落地后填充,暂为「—」。 */}
                  {member.member_type === 'agent' ? t('agents.presence.unknown') : ''}
                </td>
                <td>
                  {canManage ? (
                    <div className="mesh-members__row-actions">
                      {member.status === 'active' ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          data-testid={`disable-${member.id}`}
                          onClick={() => setConfirm({ mode: 'disable', member })}
                        >
                          {t('members.disable.action')}
                        </Button>
                      ) : null}
                      {member.status === 'disabled' ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          data-testid={`enable-${member.id}`}
                          onClick={() => handleEnable(member)}
                        >
                          {t('members.enable.action')}
                        </Button>
                      ) : null}
                      {member.status !== 'removed' && member.id !== meId ? (
                        <Button
                          size="sm"
                          variant="danger"
                          data-testid={`remove-${member.id}`}
                          onClick={() => setConfirm({ mode: 'remove', member })}
                        >
                          {t('members.remove.action')}
                        </Button>
                      ) : null}
                      {/* 上手进度重置仅对人类成员行(agent 不建清单,onboarding.md §3.5) */}
                      {member.member_type === 'human' ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          data-testid={`reset-onboarding-${member.id}`}
                          onClick={() => setResetTarget(member)}
                        >
                          {t('onboarding.reset.action')}
                        </Button>
                      ) : null}
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {detail !== null ? (
        <aside
          className="mesh-members__drawer"
          role="dialog"
          aria-label={detail.display_name}
          data-testid="member-drawer"
        >
          <h2 className="mesh-members__drawer-title">{detail.display_name}</h2>
          <dl className="mesh-members__drawer-dl">
            <dt>{t('members.col.role')}</dt>
            <dd>{t(`members.role.${detail.role}`)}</dd>
            <dt>{t('members.col.status')}</dt>
            <dd>{t(`members.status.${detail.status}`)}</dd>
            <dt>{t('members.detail.openIssues')}</dt>
            <dd>{detail.counts.open_issues_assigned}</dd>
          </dl>
          <Button variant="secondary" onClick={() => setDetail(null)}>
            {t('common.close')}
          </Button>
        </aside>
      ) : null}

      {workspace !== null ? (
        <>
          <AddMemberDialog
            open={addOpen}
            onClose={() => setAddOpen(false)}
            client={client}
            workspaceId={workspace.workspace_id}
            onInvited={() => setReloadKey((key) => key + 1)}
          />
          <AgentWizard
            open={wizardOpen}
            onClose={() => setWizardOpen(false)}
            client={client}
            workspaceId={workspace.workspace_id}
            onSaved={() => setReloadKey((key) => key + 1)}
          />
          {confirm !== null ? (
            <RemoveMemberDialog
              open
              mode={confirm.mode}
              onClose={() => setConfirm(null)}
              client={client}
              workspaceId={workspace.workspace_id}
              member={confirm.member}
              reassignTargets={reassignTargets}
              onChanged={() => setReloadKey((key) => key + 1)}
            />
          ) : null}
          <Dialog
            open={resetTarget !== null}
            onClose={() => setResetTarget(null)}
            title={t('onboarding.reset.confirmTitle')}
            closeLabel={t('common.close')}
          >
            <p data-testid="reset-onboarding-body">{t('onboarding.reset.confirmBody')}</p>
            <div className="mesh-members__dialog-actions">
              <Button variant="secondary" onClick={() => setResetTarget(null)}>
                {t('common.cancel')}
              </Button>
              {resetTarget !== null ? (
                <Button
                  variant="danger"
                  data-testid="reset-onboarding-confirm"
                  onClick={() => void handleResetOnboarding(resetTarget)}
                >
                  {t('onboarding.reset.confirm')}
                </Button>
              ) : null}
            </div>
          </Dialog>
        </>
      ) : null}
    </main>
  );
}
