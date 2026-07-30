/**
 * 成员名册页(member.md §4,README §6.12)。
 *
 * 唯一名册 + 唯一创建入口:人类与 agent 同表呈现,「仅 Agent」是同一路由
 * (`?member_type=agent`)的筛选投影 —— 同一列表组件、同一 `[ + 新建 Agent ]` 入口,
 * 不存在独立 Agents 列表页/第二导航/第二创建入口(T35)。
 *
 * 显示名由服务端按 §2.4 解析为单一 `display_name`;前端仅渲染,并据 `member_type`
 * 叠加「AI」徽章(design Badge accent)。角色/状态/显示名变更经 REST(乐观刷新后重拉名册)。
 *
 * 设计对齐(design-quality.md):头像/徽章/运行态徽标/菜单一律出自 `src/design`;
 * 排版走 type-scale 工具类;A-05 收尾——窄屏表格隐藏、改主次行卡片,行操作进底座 Menu,
 * 无横向溢出;agent 行运行态经 presence 实时帧 → `presenceToRunState` 五态统一语言(§9.8)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { MeshApiClient, getToken } from '../../api';
import {
  Avatar,
  Badge,
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Icon,
  Menu,
  RunStateBadge,
  Skeleton,
  useToast,
} from '../../design';
import type { MenuEntry } from '../../design';
import type { RunState } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { AgentWizard } from '../agents/AgentWizard';
import { useAgentPresenceMap } from '../agents/presence';
import { presenceToRunState } from '../agents/runState';
import { resetOnboardingMember } from '../onboarding/api';
import { requestOptimisticStepComplete } from '../onboarding/notify';
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

/** 名称下的次要行:人类邮箱 / agent 描述(§3.2 成员行主次分行)。 */
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

/** 头像 URL:profile.avatar_url 为空/空串回退 undefined(Avatar 据此渲染缩写/轮廓)。 */
function avatarSrc(member: MemberSummary): string | undefined {
  const profile = member.profile;
  if (profile !== null && 'avatar_url' in profile && profile.avatar_url) {
    return profile.avatar_url;
  }
  return undefined;
}

/** agent 行 role_tag(§4.2/§4.5);非 agent 或无 profile → 空串。 */
function roleTagOf(member: MemberSummary): string {
  const profile = member.profile;
  if (member.member_type === 'agent' && profile !== null && 'role_tag' in profile) {
    return profile.role_tag ?? '';
  }
  return '';
}

/** agent 行 presence 订阅用的 agent id(profile.id);非 agent / 无 profile → null。 */
function agentIdOf(member: MemberSummary): string | null {
  const profile = member.profile;
  if (member.member_type === 'agent' && profile !== null && 'id' in profile) {
    return profile.id;
  }
  return null;
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

  // presence 实时帧 → agentId → 容量三元组(shell 外恒空,§9.8 运行态五态)。
  const agentIds = useMemo(
    () => members.map((member) => agentIdOf(member)).filter((id): id is string => id !== null),
    [members],
  );
  const presenceMap = useAgentPresenceMap(agentIds);

  /** 某 agent 行当前运行态:帧未至 → unknown;三元组 → 五态归一(§9.8)。 */
  const runStateOf = (member: MemberSummary): RunState => {
    const id = agentIdOf(member);
    if (id === null) return 'unknown';
    return presenceToRunState(presenceMap.get(id) ?? null);
  };

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

  // 行操作菜单条目(桌面表格与手机卡片同源,§7.5 低频行操作进菜单):
  // disable/enable 按 status;remove(破坏性,非 removed 且非本人);reset 仅人类行。
  const buildRowActions = (member: MemberSummary): MenuEntry[] => {
    const entries: MenuEntry[] = [];
    if (member.status === 'active') {
      entries.push({
        key: `disable-${member.id}`,
        label: t('members.disable.action'),
        onSelect: () => setConfirm({ mode: 'disable', member }),
      });
    }
    if (member.status === 'disabled') {
      entries.push({
        key: `enable-${member.id}`,
        label: t('members.enable.action'),
        onSelect: () => void handleEnable(member),
      });
    }
    if (member.status !== 'removed' && member.id !== meId) {
      entries.push({
        key: `remove-${member.id}`,
        label: t('members.remove.action'),
        danger: true,
        onSelect: () => setConfirm({ mode: 'remove', member }),
      });
    }
    if (member.member_type === 'human') {
      entries.push({
        key: `reset-onboarding-${member.id}`,
        label: t('onboarding.reset.action'),
        onSelect: () => setResetTarget(member),
      });
    }
    return entries;
  };

  /** 行操作 Menu(仅 canManage 且有条目时渲染;靠右对齐避免出界,§7.5/§7.6)。 */
  const renderRowMenu = (member: MemberSummary): React.JSX.Element | null => {
    if (!canManage) return null;
    const entries = buildRowActions(member);
    if (entries.length === 0) return null;
    return (
      <Menu
        trigger={<Icon name="more-horizontal" size={16} />}
        triggerLabel={t('members.rowActions.label')}
        entries={entries}
        align="end"
      />
    );
  };

  /** 角色控件:agent 行 role_tag 文本(§4.2),人类行角色下拉。testid 桌面/卡片分流。 */
  const renderRoleControl = (member: MemberSummary, isCard: boolean): React.JSX.Element => {
    if (member.member_type === 'agent') {
      return (
        <span
          className="mesh-text-caption"
          data-testid={isCard ? `card-role-tag-${member.id}` : `member-role-tag-${member.id}`}
        >
          {roleTagOf(member)}
        </span>
      );
    }
    return (
      <select
        className="mesh-members__role-select"
        aria-label={t('members.col.role')}
        data-testid={isCard ? `card-role-select-${member.id}` : `role-select-${member.id}`}
        value={member.role}
        disabled={!canManage}
        onChange={(event) => handleRoleChange(member, event.target.value as MemberRole)}
      >
        {ROLE_ORDER.map((role) => (
          <option key={role} value={role}>
            {t(`members.role.${role}`)}
          </option>
        ))}
      </select>
    );
  };

  /** 生命周期文案:agent 优先 lifecycle_status,否则成员状态。 */
  const lifecycleLabel = (member: MemberSummary): string => {
    if (member.member_type === 'agent') {
      const profile = member.profile;
      const lifecycle =
        profile !== null && 'lifecycle_status' in profile ? profile.lifecycle_status : null;
      if (lifecycle) return t(`agents.lifecycle.${lifecycle}`);
    }
    return t(`members.status.${member.status}`);
  };

  /** 身份簇:头像 + 名称(+AI 徽章) + 次要行,整体按钮开详情(桌面/卡片同源,testid 分流)。 */
  const renderIdentity = (member: MemberSummary, isCard: boolean): React.JSX.Element => (
    <button
      type="button"
      className="mesh-members__identity"
      data-testid={isCard ? `member-card-open-${member.id}` : `member-open-${member.id}`}
      onClick={() => openDetail(member)}
    >
      <Avatar
        name={member.display_name}
        kind={member.member_type === 'agent' ? 'agent' : 'human'}
        size={32}
        src={avatarSrc(member)}
      />
      <span className="mesh-members__identity-text">
        <span className="mesh-members__identity-primary">
          <span className="mesh-members__name mesh-text-body-strong mesh-truncate" title={member.display_name}>
            {member.display_name}
          </span>
          {member.member_type === 'agent' ? (
            <span data-testid={isCard ? `card-ai-badge-${member.id}` : `ai-badge-${member.id}`}>
              <Badge tone="accent" size="sm">
                {t('members.badge.agent')}
              </Badge>
            </span>
          ) : null}
        </span>
        <span className="mesh-members__subtext mesh-text-caption mesh-truncate">
          {memberSubtext(member)}
        </span>
      </span>
    </button>
  );

  /** agent 行运行态徽标(§9.8 五态统一语言);人类行渲染空。testid 桌面/卡片分流。 */
  const renderRunState = (member: MemberSummary, isCard: boolean): React.JSX.Element | null => {
    if (member.member_type !== 'agent') return null;
    const state = runStateOf(member);
    return (
      <span data-testid={isCard ? `card-member-presence-${member.id}` : `member-presence-${member.id}`}>
        <RunStateBadge state={state} label={t(`runState.${state}`)} size="sm" />
      </span>
    );
  };

  return (
    <main className="mesh-members">
      <div className="mesh-members__header">
        <h1 className="mesh-members__title mesh-text-title-1">{t('members.title')}</h1>
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
              className="mesh-members__tab mesh-text-body"
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
        <>
          {/* 桌面表格(0–599px 隐藏,改卡片):受控横向滚动容器,首列粘住(A-05/§7.6)。 */}
          <div className="mesh-members__table-wrap">
            <table className="mesh-members__table">
              <thead>
                <tr>
                  <th scope="col">{t('members.col.name')}</th>
                  <th scope="col">{t('agents.roster.type')}</th>
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
                    data-testid={`member-row-${member.id}`}
                    className={
                      member.status === 'removed'
                        ? 'mesh-members__row mesh-members__row--removed'
                        : 'mesh-members__row'
                    }
                  >
                    <td>{renderIdentity(member, false)}</td>
                    <td
                      className="mesh-members__sub mesh-text-body-sm"
                      data-testid={`member-type-${member.id}`}
                    >
                      {member.member_type === 'agent'
                        ? t('agents.roster.typeAgent')
                        : t('agents.roster.typeHuman')}
                    </td>
                    <td>{renderRoleControl(member, false)}</td>
                    <td
                      className="mesh-members__sub mesh-text-caption"
                      data-testid={`member-lifecycle-${member.id}`}
                    >
                      {lifecycleLabel(member)}
                    </td>
                    <td className="mesh-members__sub">{renderRunState(member, false)}</td>
                    <td>{renderRowMenu(member)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* A-05 手机卡片(默认隐藏,0–599px 显示):主次行,行操作进菜单,无横向溢出。 */}
          <ul className="mesh-members__cards">
            {members.map((member) => (
              <li
                key={member.id}
                className={
                  member.status === 'removed'
                    ? 'mesh-members__card mesh-members__card--removed'
                    : 'mesh-members__card'
                }
                data-testid={`member-card-${member.id}`}
              >
                <div className="mesh-members__card-primary">
                  {renderIdentity(member, true)}
                  {renderRowMenu(member)}
                </div>
                <div className="mesh-members__card-secondary">
                  <span className="mesh-text-caption" data-testid={`card-type-${member.id}`}>
                    {member.member_type === 'agent'
                      ? t('agents.roster.typeAgent')
                      : t('agents.roster.typeHuman')}
                  </span>
                  {renderRoleControl(member, true)}
                  <span className="mesh-text-caption" data-testid={`card-lifecycle-${member.id}`}>
                    {lifecycleLabel(member)}
                  </span>
                  {renderRunState(member, true)}
                </div>
                {member.joined_at !== null ? (
                  <span className="mesh-members__card-joined mesh-text-caption mesh-tnum">
                    {t('members.joined', { date: new Date(member.joined_at) })}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      )}

      {detail !== null ? (
        <aside
          className="mesh-members__drawer"
          role="dialog"
          aria-label={detail.display_name}
          data-testid="member-drawer"
        >
          <h2 className="mesh-members__drawer-title mesh-text-title-3">{detail.display_name}</h2>
          <dl className="mesh-members__drawer-dl">
            <dt className="mesh-text-caption">{t('members.col.role')}</dt>
            <dd className="mesh-text-body">{t(`members.role.${detail.role}`)}</dd>
            <dt className="mesh-text-caption">{t('members.col.status')}</dt>
            <dd className="mesh-text-body">{t(`members.status.${detail.status}`)}</dd>
            <dt className="mesh-text-caption">{t('members.detail.openIssues')}</dt>
            <dd className="mesh-text-body mesh-tnum">{detail.counts.open_issues_assigned}</dd>
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
            onInvited={() => {
              setReloadKey((key) => key + 1);
              requestOptimisticStepComplete('invite_member_or_add_agent'); // §1.2.2 乐观推进步骤 2
            }}
          />
          <AgentWizard
            open={wizardOpen}
            onClose={() => setWizardOpen(false)}
            client={client}
            workspaceId={workspace.workspace_id}
            onSaved={() => {
              setReloadKey((key) => key + 1);
              requestOptimisticStepComplete('invite_member_or_add_agent'); // §1.2.2 O9:加 agent 完成 → 步骤 2
            }}
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
