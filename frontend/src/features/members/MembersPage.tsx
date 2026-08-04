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
  DataView,
  Dialog,
  Drawer,
  EmptyState,
  ErrorState,
  Icon,
  Menu,
  RunStateBadge,
  Skeleton,
  Tabs,
  useToast,
} from '../../design';
import type { MenuEntry } from '../../design';
import type { RunState } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { workspaceChannel } from '../../workspace/WorkspaceProvider';
import { AgentWizard } from '../agents/AgentWizard';
import { deleteAgent, transitionAgentLifecycle } from '../agents/api';
import { useAgentPresenceMap } from '../agents/presence';
import { presenceToRunState } from '../agents/runState';
import { resetOnboardingMember } from '../onboarding/api';
import { requestOptimisticStepComplete } from '../onboarding/notify';
import { EmptyRoster } from '../onboarding/illustrations';
import { getMember, listMembers, updateMember } from './api';
import { AddMemberDialog } from './AddMemberDialog';
import { RemoveMemberDialog } from './RemoveMemberDialog';
import type { RemoveMode } from './RemoveMemberDialog';
import type { MemberDetail, MemberRole, MemberSummary, MemberType } from './types';
import { ROLE_ORDER } from './types';
import { useWorkspaceMembership, workspaceRoute } from './useWorkspaceMembership';
import './members.css';

const SEARCH_DEBOUNCE_MS = 300;
const MEMBER_EVENTS = new Set([
  'member.added',
  'member.updated',
  'member.removed',
  // The current backend keeps role changes explicit on the workspace channel.
  'member.role_changed',
]);

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

function agentLifecycleOf(member: MemberSummary): string | null {
  const profile = member.profile;
  if (member.member_type === 'agent' && profile !== null && 'lifecycle_status' in profile) {
    return profile.lifecycle_status ?? null;
  }
  return null;
}

function isCurrentHumanMember(member: MemberSummary, userId: string | null): boolean {
  return (
    userId !== null &&
    member.member_type === 'human' &&
    member.profile !== null &&
    'id' in member.profile &&
    member.profile.id === userId
  );
}

export function MembersPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const membershipState = useWorkspaceMembership(client);
  const workspace = membershipState.kind === 'ready' ? membershipState.membership : null;
  const currentUserId = membershipState.kind === 'ready' ? membershipState.user.id : null;

  const [searchParams, setSearchParams] = useSearchParams();
  const memberTypeParam = searchParams.get('member_type');
  const statusParam = searchParams.get('status');
  const activeTab = tabFromParams(memberTypeParam, statusParam);

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
  const [agentActionPending, setAgentActionPending] = useState(false);
  const [agentActionError, setAgentActionError] = useState<string | null>(null);
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

  // Debounce the search box into the query term.
  useEffect(() => {
    const handle = setTimeout(() => setQ(searchInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [searchInput]);

  const loadRoster = useCallback(() => {
    if (membershipState.kind !== 'ready' || workspace === null) {
      setIsLoading(membershipState.kind === 'loading');
      if (membershipState.kind === 'error') setError(t('state.errorDescription'));
      else setError(null);
      if (membershipState.kind === 'no_workspace') setMembers([]);
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
  }, [client, workspace, membershipState.kind, activeTab, q, t]);

  useEffect(() => {
    loadRoster();
  }, [loadRoster, reloadKey]);

  // Member mutations are projected onto the workspace channel by the current
  // backend. Reload the active projection only for this workspace/channel.
  useEffect(() => {
    if (realtime === null || workspace === null) return;
    const channel = workspaceChannel(workspace.workspace_id);
    realtime.client.subscribe(channel);
    const unsubscribe = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel || !MEMBER_EVENTS.has(frame.event)) return;
      setReloadKey((key) => key + 1);
    });
    return () => {
      unsubscribe();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspace]);

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

  // 乐观更新某成员状态:让行菜单在 reload 骨架/网络往返窗口内即反映新态,
  // 杜绝「停用/启用后重开菜单仍按旧态计算条目」的竞态(验收 R4:Remove 120s 超时)。
  const patchMemberStatus = useCallback(
    (memberId: string, status: MemberSummary['status']): void => {
      setMembers((prev) => prev.map((item) => (item.id === memberId ? { ...item, status } : item)));
    },
    [],
  );

  const handleEnable = async (member: MemberSummary): Promise<void> => {
    if (workspace === null) return;
    patchMemberStatus(member.id, 'active');
    try {
      const agentId = agentIdOf(member);
      if (agentId !== null) {
        await transitionAgentLifecycle(client, workspace.workspace_id, agentId, 'enable');
      } else {
        await updateMember(client, workspace.workspace_id, member.id, { status: 'active' });
      }
      setReloadKey((key) => key + 1);
    } catch (err) {
      patchMemberStatus(member.id, member.status);
      toast.addToast(err instanceof Error ? err.message : t('common.unknownError'), {
        tone: 'danger',
        closeLabel: t('common.close'),
      });
    }
  };

  const confirmAgentAction = async (): Promise<void> => {
    if (workspace === null || confirm === null) return;
    const agentId = agentIdOf(confirm.member);
    if (agentId === null) return;
    setAgentActionPending(true);
    setAgentActionError(null);
    try {
      if (confirm.mode === 'disable') {
        await transitionAgentLifecycle(client, workspace.workspace_id, agentId, 'disable');
        patchMemberStatus(confirm.member.id, 'disabled');
      } else {
        await deleteAgent(client, workspace.workspace_id, agentId);
        patchMemberStatus(confirm.member.id, 'removed');
      }
      setConfirm(null);
      setReloadKey((key) => key + 1);
    } catch (err) {
      setAgentActionError(err instanceof Error ? err.message : t('common.unknownError'));
    } finally {
      setAgentActionPending(false);
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
    if (
      workspace !== null &&
      member.member_type === 'agent' &&
      member.profile !== null &&
      'id' in member.profile
    ) {
      navigate(workspaceRoute(workspace.workspace_slug, `/agents/${member.profile.id}`));
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
    const lifecycle = agentLifecycleOf(member);
    const canDisable =
      member.member_type === 'agent'
        ? lifecycle === 'active' || lifecycle === 'paused'
        : member.status === 'active';
    const canEnable =
      member.member_type === 'agent' ? lifecycle === 'disabled' : member.status === 'disabled';
    if (canDisable) {
      entries.push({
        key: `disable-${member.id}`,
        label: t('members.disable.action'),
        onSelect: () => {
          setAgentActionError(null);
          setConfirm({ mode: 'disable', member });
        },
      });
    }
    if (canEnable) {
      entries.push({
        key: `enable-${member.id}`,
        label: t('members.enable.action'),
        onSelect: () => void handleEnable(member),
      });
    }
    const canRemove = member.member_type === 'human' || agentIdOf(member) !== null;
    if (canRemove && member.status !== 'removed' && !isCurrentHumanMember(member, currentUserId)) {
      entries.push({
        key: `remove-${member.id}`,
        label: t('members.remove.action'),
        danger: true,
        onSelect: () => {
          setAgentActionError(null);
          setConfirm({ mode: 'remove', member });
        },
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

  /** 角色控件:人类与 agent 共用 workspace role;agent 永远不能成为 owner。 */
  const renderRoleControl = (member: MemberSummary, isCard: boolean): React.JSX.Element => {
    const select = (
      <select
        className="mesh-members__role-select"
        aria-label={t('members.col.role')}
        data-testid={isCard ? `card-role-select-${member.id}` : `role-select-${member.id}`}
        value={member.role}
        disabled={!canManage}
        onChange={(event) => handleRoleChange(member, event.target.value as MemberRole)}
      >
        {ROLE_ORDER.map((role) => (
          <option
            key={role}
            value={role}
            disabled={member.member_type === 'agent' && role === 'owner'}
          >
            {t(`members.role.${role}`)}
          </option>
        ))}
      </select>
    );
    if (member.member_type === 'agent') {
      return (
        <span className="mesh-members__role-control">
          {select}
          <span
            className="mesh-text-caption"
            data-testid={isCard ? `card-role-tag-${member.id}` : `member-role-tag-${member.id}`}
          >
            {roleTagOf(member)}
          </span>
        </span>
      );
    }
    return select;
  };

  /** 生命周期文案:agent 优先 lifecycle_status,否则成员状态。 */
  const lifecycleLabel = (member: MemberSummary): string => {
    const lifecycle = agentLifecycleOf(member);
    if (lifecycle) return t(`agents.lifecycle.${lifecycle}`);
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
          <span
            className="mesh-members__name mesh-text-body-strong mesh-truncate"
            title={member.display_name}
          >
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
      <span
        data-testid={isCard ? `card-member-presence-${member.id}` : `member-presence-${member.id}`}
      >
        <RunStateBadge state={state} label={t(`runState.${state}`)} size="sm" />
      </span>
    );
  };

  return (
    <>
      <DataView
        className="mesh-members"
        title={t('members.title')}
        actions={
          canManage ? (
            <div className="mesh-members__actions">
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
            </div>
          ) : undefined
        }
        toolbar={
          <div className="mesh-members__toolbar">
            <Tabs
              className="mesh-members__filter-tabs"
              label={t('members.filterLabel')}
              value={activeTab}
              onChange={(value) => selectTab(value as TabKey)}
              items={(['all', 'human', 'agent', 'disabled'] as const).map((tab) => ({
                value: tab,
                label: t(`members.tab.${tab}`),
                content: null,
                testId: `tab-${tab}`,
              }))}
            />
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
        }
      >
        {workspace === null && !isLoading && error === null ? (
          <EmptyState title={t('state.emptyTitle')} description={t('members.noWorkspace')} />
        ) : error !== null ? (
          <ErrorState
            title={t('state.errorTitle')}
            description={error}
            retryLabel={t('common.retry')}
            onRetry={
              membershipState.kind === 'error'
                ? membershipState.retry
                : () => setReloadKey((key) => key + 1)
            }
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
                <caption className="sr-only">{t('members.title')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('members.col.name')}</th>
                    <th scope="col">{t('agents.roster.type')}</th>
                    <th scope="col">{t('members.col.role')}</th>
                    <th scope="col">{t('agents.roster.lifecycle')}</th>
                    <th scope="col">{t('agents.roster.presence')}</th>
                    <th scope="col">{t('issues.detail.activity')}</th>
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
                      <td
                        className="mesh-members__activity mesh-text-caption mesh-tnum"
                        data-testid={`member-activity-${member.id}`}
                      >
                        {member.joined_at === null
                          ? '—'
                          : t('members.joined', { date: new Date(member.joined_at) })}
                      </td>
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
      </DataView>

      {detail !== null ? (
        <Drawer
          open
          onClose={() => setDetail(null)}
          title={detail.display_name}
          closeLabel={t('common.close')}
        >
          <dl className="mesh-members__drawer-dl" data-testid="member-drawer">
            <dt className="mesh-text-caption">{t('members.col.role')}</dt>
            <dd className="mesh-text-body">{t(`members.role.${detail.role}`)}</dd>
            <dt className="mesh-text-caption">{t('members.col.status')}</dt>
            <dd className="mesh-text-body">{t(`members.status.${detail.status}`)}</dd>
            <dt className="mesh-text-caption">{t('members.detail.openIssues')}</dt>
            <dd className="mesh-text-body mesh-tnum">{detail.counts.open_issues_assigned}</dd>
          </dl>
        </Drawer>
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
          {confirm !== null && confirm.member.member_type === 'agent' ? (
            <Dialog
              open
              onClose={() => setConfirm(null)}
              title={
                confirm.mode === 'remove' ? t('members.remove.title') : t('members.disable.title')
              }
              closeLabel={t('common.close')}
            >
              <div className="mesh-members__dialog-body">
                <p>
                  {confirm.mode === 'remove'
                    ? t('members.remove.confirm', { name: confirm.member.display_name })
                    : t('members.disable.confirm', { name: confirm.member.display_name })}
                </p>
                {agentActionError !== null ? (
                  <p className="mesh-members__error">{agentActionError}</p>
                ) : null}
                <div className="mesh-members__dialog-footer">
                  <Button variant="secondary" onClick={() => setConfirm(null)}>
                    {t('common.cancel')}
                  </Button>
                  <Button
                    variant={confirm.mode === 'remove' ? 'danger' : 'primary'}
                    onClick={() => void confirmAgentAction()}
                    isLoading={agentActionPending}
                    data-testid="remove-confirm"
                  >
                    {confirm.mode === 'remove'
                      ? t('members.remove.submit')
                      : t('members.disable.submit')}
                  </Button>
                </div>
              </div>
            </Dialog>
          ) : confirm !== null ? (
            <RemoveMemberDialog
              open
              mode={confirm.mode}
              onClose={() => setConfirm(null)}
              client={client}
              workspaceId={workspace.workspace_id}
              member={confirm.member}
              reassignTargets={reassignTargets}
              onChanged={() => {
                // 乐观更新:disable→disabled、remove→removed,使行菜单立即按新态重算。
                patchMemberStatus(
                  confirm.member.id,
                  confirm.mode === 'remove' ? 'removed' : 'disabled',
                );
                setReloadKey((key) => key + 1);
              }}
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
    </>
  );
}
