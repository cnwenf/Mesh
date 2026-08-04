/**
 * 小队详情页(squad.md §4.2–§4.5):
 * 头部(名称 / 状态点 / 归档·恢复)+ 成员面板(角色徽标 / 加成员 / 改角色 / 移除)+
 * 当前任务列表(深链任务详情)+ 协作时间线(按 action 过滤)+ 消息区(kind Tab + 发送)。
 * 实时经 squad:{id} 频道,收到帧即整体重载(§3.5)。
 * 状态渲染序:错误态(可重试)→ 骨架 → 内容。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { useIntl } from 'react-intl';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, Dialog, ErrorState, Select, Skeleton, StatusDot, useToast } from '../../design';
import { env } from '../../env';
import { formatDateTime, useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { listMembers as listWorkspaceMembers } from '../members/api';
import type { MemberSummary, Membership } from '../members/types';
import { useWorkspaceMembership, workspaceRoute } from '../members/useWorkspaceMembership';
import {
  addMembers,
  archiveSquad,
  changeRole,
  listActivity,
  listMembers,
  listMessages,
  listTasks,
  removeMember,
  restoreSquad,
  sendMessage,
  squadChannel,
  getSquad,
} from './api';
import { EditSquadDialog } from './EditSquadDialog';
import type {
  MessageKind,
  Squad,
  SquadActivity,
  SquadMember,
  SquadMessage,
  SquadRole,
  SquadTask,
} from './types';
import { MESSAGE_KIND_ORDER, SQUAD_ROLE_ORDER, TASK_STATUS_TONE } from './types';
import './squads.css';

const ALL = 'all';
const ROSTER_LIMIT = 100;

function timestamp(iso: string, locale: string): string {
  try {
    return formatDateTime(iso, { locale, timeZone: 'UTC', dateStyle: 'short', timeStyle: 'short' });
  } catch {
    return iso;
  }
}

/* ---- 成员面板 ---- */

interface MembersPaneProps {
  readonly workspace: Membership;
  readonly squad: Squad;
  readonly members: readonly SquadMember[];
  /** 角色变更成功:父级就地更新该成员角色(不可变拷贝)。 */
  readonly onRoleChanged: (memberId: string, role: SquadRole) => void;
  /** 移除成功:父级就地剔除该成员。 */
  readonly onRemoved: (memberId: string) => void;
  /** 加成员成功:父级重拉成员名册。 */
  readonly onAdded: () => void;
  readonly canManage: boolean;
}

function MembersPane(props: MembersPaneProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [addOpen, setAddOpen] = useState(false);
  const [roster, setRoster] = useState<MemberSummary[]>([]);
  const [pickMemberId, setPickMemberId] = useState('');
  const [pickRole, setPickRole] = useState<SquadRole>('member');
  const { workspace, squad, members, onRoleChanged, onRemoved, onAdded, canManage } = props;

  useEffect(() => {
    if (!addOpen) return;
    let cancelled = false;
    void (async () => {
      const page = await listWorkspaceMembers(client, workspace.workspace_id, {
        limit: ROSTER_LIMIT,
      });
      if (cancelled) return;
      setRoster([...page.data]);
    })();
    return () => {
      cancelled = true;
    };
  }, [addOpen, client, workspace.workspace_id]);

  const notify = useCallback(
    (key: string) => toast.addToast(t(key), { tone: 'success', closeLabel: t('common.close') }),
    [toast, t],
  );

  const onChangeRole = useCallback(
    async (memberId: string, role: SquadRole) => {
      try {
        await changeRole(client, workspace.workspace_id, squad.id, memberId, role);
        notify('squads.toast.roleChanged');
        onRoleChanged(memberId, role);
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [client, workspace.workspace_id, squad.id, notify, onRoleChanged, toast, t],
  );

  const onRemove = useCallback(
    async (memberId: string) => {
      try {
        await removeMember(client, workspace.workspace_id, squad.id, memberId);
        notify('squads.toast.memberRemoved');
        onRemoved(memberId);
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [client, workspace.workspace_id, squad.id, notify, onRemoved, toast, t],
  );

  const onAdd = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (pickMemberId === '') return;
      try {
        await addMembers(client, workspace.workspace_id, squad.id, [
          { member_id: pickMemberId, role: pickRole },
        ]);
        notify('squads.toast.memberAdded');
        setAddOpen(false);
        setPickMemberId('');
        onAdded();
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [client, workspace.workspace_id, squad.id, pickMemberId, pickRole, notify, onAdded, toast, t],
  );

  const existingIds = new Set(members.map((m) => m.member_id));
  const candidates = roster.filter((m) => !existingIds.has(m.id));

  return (
    <section className="mesh-squads__pane" data-testid="squad-members-pane">
      <div className="mesh-squads__pane-head">
        <h2>{t('squads.members')}</h2>
        {canManage ? (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setAddOpen(true)}
            data-testid="squad-add-member"
          >
            {t('squads.detail.addMember')}
          </Button>
        ) : null}
      </div>
      {members.length === 0 ? (
        <p className="mesh-squads__pane-empty">{t('squads.detail.noMembers')}</p>
      ) : (
        <ul className="mesh-squads__members">
          {members.map((member) => (
            <li
              key={member.id}
              className="mesh-squads__member"
              data-testid={`squad-member-${member.member_id}`}
            >
              <span className="mesh-squads__member-name">{member.name}</span>
              <span className="mesh-squads__member-type">
                {member.member_type === 'agent' ? t('squads.agentBadge') : t('squads.humanBadge')}
              </span>
              <Select
                label={t('squads.detail.role')}
                value={member.role}
                disabled={!canManage}
                data-testid={`squad-member-role-${member.member_id}`}
                onChange={(event) =>
                  onChangeRole(member.member_id, event.target.value as SquadRole)
                }
              >
                {SQUAD_ROLE_ORDER.map((role) => (
                  <option key={role} value={role}>
                    {t(`squads.role.${role}`)}
                  </option>
                ))}
              </Select>
              {canManage ? (
                <Button
                  size="sm"
                  variant="danger"
                  data-testid={`squad-member-remove-${member.member_id}`}
                  onClick={() => void onRemove(member.member_id)}
                >
                  {t('squads.detail.remove')}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {addOpen ? (
        <Dialog
          open
          onClose={() => setAddOpen(false)}
          title={t('squads.detail.addMember')}
          closeLabel={t('common.close')}
        >
          <form
            className="mesh-squads__form"
            data-testid="squad-add-member-form"
            onSubmit={(event) => void onAdd(event)}
          >
            <Select
              label={t('squads.detail.selectMember')}
              value={pickMemberId}
              data-testid="squad-add-member-select"
              onChange={(event) => setPickMemberId(event.target.value)}
            >
              <option value="">{t('squads.detail.selectMember')}</option>
              {candidates.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
            </Select>
            <Select
              label={t('squads.detail.role')}
              value={pickRole}
              data-testid="squad-add-member-role"
              onChange={(event) => setPickRole(event.target.value as SquadRole)}
            >
              {SQUAD_ROLE_ORDER.map((role) => (
                <option key={role} value={role}>
                  {t(`squads.role.${role}`)}
                </option>
              ))}
            </Select>
            <div className="mesh-squads__form-actions">
              <Button type="submit" disabled={pickMemberId === ''}>
                {t('squads.detail.add')}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setAddOpen(false)}>
                {t('common.cancel')}
              </Button>
            </div>
          </form>
        </Dialog>
      ) : null}
    </section>
  );
}

/* ---- 消息面板 ---- */

interface MessagesPaneProps {
  readonly workspace: Membership;
  readonly squad: Squad;
  readonly messages: readonly SquadMessage[];
  /** 发送成功:父级就地把新消息追加到列表。 */
  readonly onSent: (message: SquadMessage) => void;
}

function MessagesPane(props: MessagesPaneProps): React.JSX.Element {
  const t = useT();
  const intl = useIntl();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [kindTab, setKindTab] = useState<string>(ALL);
  const [draft, setDraft] = useState('');
  const [draftKind, setDraftKind] = useState<MessageKind>('chat');
  const { workspace, squad, messages, onSent } = props;

  const visible =
    kindTab === ALL ? messages : messages.filter((message) => message.kind === kindTab);

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (draft.trim() === '') return;
      try {
        const created = await sendMessage(client, workspace.workspace_id, squad.id, {
          kind: draftKind,
          body_markdown: draft.trim(),
        });
        setDraft('');
        toast.addToast(t('squads.toast.messageSent'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
        onSent(created);
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [client, workspace.workspace_id, squad.id, draft, draftKind, toast, t, onSent],
  );

  return (
    <section className="mesh-squads__pane" data-testid="squad-messages-pane">
      <h2>{t('squads.messages')}</h2>
      <div className="mesh-squads__tabs" role="tablist" aria-label={t('squads.messages')}>
        <button
          type="button"
          role="tab"
          aria-selected={kindTab === ALL}
          className="mesh-squads__tab"
          data-testid="squad-msgtab-all"
          onClick={() => setKindTab(ALL)}
        >
          {t('squads.messageKind.all')}
        </button>
        {MESSAGE_KIND_ORDER.map((kind) => (
          <button
            key={kind}
            type="button"
            role="tab"
            aria-selected={kindTab === kind}
            className="mesh-squads__tab"
            data-testid={`squad-msgtab-${kind}`}
            onClick={() => setKindTab(kind)}
          >
            {t(`squads.messageKind.${kind}`)}
          </button>
        ))}
      </div>
      {visible.length === 0 ? (
        <p className="mesh-squads__pane-empty" data-testid="squad-messages-empty">
          {t('squads.detail.noMessages')}
        </p>
      ) : (
        <ul className="mesh-squads__messages">
          {visible.map((message) => (
            <li
              key={message.id}
              className="mesh-squads__message"
              data-testid={`squad-message-${message.id}`}
            >
              <span className="mesh-squads__message-sender">
                {message.sender !== null ? message.sender.name : t('squads.messageKind.system')}
              </span>
              <span className="mesh-squads__message-kind">
                {t(`squads.messageKind.${message.kind}`)}
              </span>
              <span className="mesh-squads__message-time">
                {timestamp(message.created_at, intl.locale)}
              </span>
              <p className="mesh-squads__message-body">{message.body_markdown}</p>
            </li>
          ))}
        </ul>
      )}
      <form
        className="mesh-squads__composer"
        data-testid="squad-composer"
        onSubmit={(event) => void submit(event)}
      >
        <Select
          label={t('squads.kind')}
          value={draftKind}
          data-testid="squad-composer-kind"
          onChange={(event) => setDraftKind(event.target.value as MessageKind)}
        >
          {MESSAGE_KIND_ORDER.filter((kind) => kind !== 'system').map((kind) => (
            <option key={kind} value={kind}>
              {t(`squads.messageKind.${kind}`)}
            </option>
          ))}
        </Select>
        <label className="mesh-squads__composer-body">
          <span>{t('squads.detail.messageLabel')}</span>
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={t('squads.detail.messagePlaceholder')}
            data-testid="squad-composer-body"
            rows={2}
          />
        </label>
        <Button type="submit" disabled={draft.trim() === ''} data-testid="squad-composer-send">
          {t('squads.detail.send')}
        </Button>
      </form>
    </section>
  );
}

/* ---- 页面主体 ---- */

export function SquadDetailPage(): React.JSX.Element {
  const t = useT();
  const intl = useIntl();
  const toast = useToast();
  const { squadId } = useParams<{ squadId: string }>();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const membershipState = useWorkspaceMembership(client);
  const workspace = membershipState.kind === 'ready' ? membershipState.membership : null;
  const canManage = workspace?.role === 'owner' || workspace?.role === 'admin';

  const [squad, setSquad] = useState<Squad | null>(null);
  const [members, setMembers] = useState<SquadMember[]>([]);
  const [tasks, setTasks] = useState<SquadTask[]>([]);
  const [activity, setActivity] = useState<SquadActivity[]>([]);
  const [messages, setMessages] = useState<SquadMessage[]>([]);
  const [actionFilter, setActionFilter] = useState(ALL);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [editOpen, setEditOpen] = useState(false);

  const tRef = useRef(t);
  tRef.current = t;

  const load = useCallback(async (): Promise<void> => {
    if (workspace === null || squadId === undefined) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const detail = await getSquad(client, workspace.workspace_id, squadId);
      const [memberList, taskList, activityList, messageList] = await Promise.all([
        listMembers(client, workspace.workspace_id, squadId),
        listTasks(client, workspace.workspace_id, squadId),
        listActivity(client, workspace.workspace_id, squadId),
        listMessages(client, workspace.workspace_id, squadId),
      ]);
      setSquad(detail);
      setMembers([...memberList]);
      setTasks([...taskList]);
      setActivity([...activityList]);
      setMessages([...messageList]);
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      setError(tRef.current(key));
    } finally {
      setIsLoading(false);
    }
  }, [client, workspace, squadId]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  // 实时:订阅 squad:{id},收帧即整体重载(§3.5)。
  useEffect(() => {
    if (realtime === null || squadId === undefined) return;
    const channel = squadChannel(squadId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      if (frame.channel !== channel) return;
      setReloadKey((k) => k + 1);
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, squadId]);

  const toggleArchive = useCallback(async (): Promise<void> => {
    if (workspace === null || squad === null) return;
    try {
      const updated =
        squad.status === 'active'
          ? await archiveSquad(client, workspace.workspace_id, squad.id)
          : await restoreSquad(client, workspace.workspace_id, squad.id);
      setSquad(updated);
      toast.addToast(
        t(squad.status === 'active' ? 'squads.toast.archived' : 'squads.toast.restored'),
        { tone: 'success', closeLabel: t('common.close') },
      );
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    }
  }, [client, workspace, squad, toast, t]);

  // 成员就地更新(改角色 / 移除)与名册重拉(加成员后)——避免整页级联重拉。
  const handleRoleChanged = useCallback((memberId: string, role: SquadRole) => {
    setMembers((prev) => prev.map((m) => (m.member_id === memberId ? { ...m, role } : m)));
  }, []);
  const handleRemoved = useCallback((memberId: string) => {
    setMembers((prev) => prev.filter((m) => m.member_id !== memberId));
  }, []);
  const refreshMembers = useCallback((): void => {
    if (workspace === null || squadId === undefined) return;
    void (async () => {
      try {
        const list = await listMembers(client, workspace.workspace_id, squadId);
        setMembers([...list]);
      } catch {
        // 名册重拉失败保持现有列表,不打断页面。
      }
    })();
  }, [client, workspace, squadId]);
  const handleMessageSent = useCallback((message: SquadMessage) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  if (membershipState.kind === 'error') {
    return <ErrorState title={t('state.errorTitle')} description={t('state.errorDescription')} />;
  }
  if (membershipState.kind === 'no_workspace') {
    return <ErrorState title={t('state.emptyTitle')} description={t('squads.noWorkspace')} />;
  }
  if (error !== null) {
    return (
      <ErrorState
        title={t('state.errorTitle')}
        description={error}
        retryLabel={t('common.retry')}
        onRetry={() => setReloadKey((k) => k + 1)}
      />
    );
  }
  if (membershipState.kind === 'loading' || isLoading || squad === null || workspace === null) {
    return <Skeleton loadingLabel={t('common.loading')} />;
  }

  const visibleActivity =
    actionFilter === ALL ? activity : activity.filter((entry) => entry.action === actionFilter);
  const actionOptions = [...new Set(activity.map((entry) => entry.action))];

  return (
    <div className="mesh-squads" data-testid="squad-detail-page">
      <header className="mesh-squads__head">
        <h1 data-testid="squad-title">{squad.name}</h1>
        <StatusDot
          tone={squad.status === 'active' ? 'success' : 'neutral'}
          label={t(`squads.status.${squad.status}`)}
        />
        {canManage ? (
          <>
            <Button
              variant="secondary"
              onClick={() => setEditOpen(true)}
              data-testid="squad-edit-toggle"
            >
              {t('squads.edit')}
            </Button>
            <Button
              variant="secondary"
              onClick={() => void toggleArchive()}
              data-testid="squad-archive-toggle"
            >
              {squad.status === 'active' ? t('squads.archive') : t('squads.restore')}
            </Button>
          </>
        ) : null}
      </header>

      <MembersPane
        workspace={workspace}
        squad={squad}
        members={members}
        onRoleChanged={handleRoleChanged}
        onRemoved={handleRemoved}
        onAdded={refreshMembers}
        canManage={canManage}
      />

      <section className="mesh-squads__pane" data-testid="squad-tasks-pane">
        <h2>{t('squads.detail.tasks')}</h2>
        {tasks.length === 0 ? (
          <p className="mesh-squads__pane-empty">{t('squads.detail.noTasks')}</p>
        ) : (
          <ul className="mesh-squads__tasks">
            {tasks.map((task) => (
              <li key={task.id} className="mesh-squads__task" data-testid={`squad-task-${task.id}`}>
                <Link
                  to={workspaceRoute(
                    workspace.workspace_slug,
                    `/squads/${squad.id}/tasks/${task.id}`,
                  )}
                  className="mesh-squads__task-link"
                >
                  {task.title_snapshot ?? task.id}
                </Link>
                <StatusDot
                  tone={TASK_STATUS_TONE[task.status]}
                  label={t(`squads.task.status.${task.status}`)}
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mesh-squads__pane" data-testid="squad-activity-pane">
        <div className="mesh-squads__pane-head">
          <h2>{t('squads.activity')}</h2>
          <Select
            label={t('squads.detail.filterAction')}
            value={actionFilter}
            data-testid="squad-activity-filter"
            onChange={(event) => setActionFilter(event.target.value)}
          >
            <option value={ALL}>{t('squads.detail.allActions')}</option>
            {actionOptions.map((action) => (
              <option key={action} value={action}>
                {t(`squads.activity.action.${action}`)}
              </option>
            ))}
          </Select>
        </div>
        {visibleActivity.length === 0 ? (
          <p className="mesh-squads__pane-empty">{t('squads.detail.noActivity')}</p>
        ) : (
          <ul className="mesh-squads__activity">
            {visibleActivity.map((entry) => (
              <li
                key={entry.id}
                className="mesh-squads__activity-item"
                data-testid={`squad-activity-${entry.id}`}
              >
                <span className="mesh-squads__activity-action">
                  {t(`squads.activity.action.${entry.action}`)}
                </span>
                <span className="mesh-squads__activity-actor">
                  {entry.actor !== null ? entry.actor.name : t('squads.messageKind.system')}
                </span>
                <span className="mesh-squads__activity-time">
                  {timestamp(entry.created_at, intl.locale)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <MessagesPane
        workspace={workspace}
        squad={squad}
        messages={messages}
        onSent={handleMessageSent}
      />

      {editOpen && canManage ? (
        <EditSquadDialog
          workspace={workspace}
          squad={squad}
          onSaved={(updated) => {
            setSquad(updated);
            toast.addToast(t('squads.toast.updated'), {
              tone: 'success',
              closeLabel: t('common.close'),
            });
          }}
          onClose={() => setEditOpen(false)}
        />
      ) : null}
    </div>
  );
}
