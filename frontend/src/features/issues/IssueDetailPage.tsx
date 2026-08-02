/**
 * Issue 详情页(design-quality.md §3.2 详情行 / §8.3;issue.md §4.1-§4.3):
 * DetailLayout 模板 —— 对象头(编号 + 内联标题编辑 + 版本 + 保存态弱提示 + 动作菜单)、
 * 标题下 summary chips(状态/优先级/负责人/截止日,桌面手机均可见)、主内容
 * (描述/子项/依赖/附件 + 评论·活动 Tabs 切换)、属性经 aside 槽呈现(桌面 320px
 * 侧栏,窄屏自动收为「属性」按钮 + 底部 sheet)。
 * 乐观更新 + version 冲突收敛(useOptimisticMutation,If-Match: updated_at);
 * 保存/冲突态经 useSaveIndicator 弱提示(§3.2:保存与冲突状态清楚)。
 * 实时经 issue:{id} 频道按 id 合并(§3.6/§6.7)。
 */
/* eslint-disable react-refresh/only-export-components -- categoryTone/saveIndicatorText 为页面内纯助手,与组件同模块契约 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken, useOptimisticMutation } from '../../api';
import {
  Avatar,
  Badge,
  Button,
  DetailLayout,
  Dialog,
  ErrorState,
  Icon,
  Menu,
  Select,
  Skeleton,
  Tabs,
  useToast,
} from '../../design';
import type { BadgeTone } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { usePageContext, useShortcutRegistry } from '../../shortcuts';
import { AttachmentPanel } from '../attachments';
import { useSettingsStore } from '../../state/settingsStore';
import { CommentsPanel } from '../comments';
import type { CommentMemberRef } from '../comments';
import type { MentionCandidate } from '../comments/mentions';
import { fetchMe, listMembers } from '../members/api';
import type { HumanProfile, MemberSummary } from '../members/types';
import { listCycles, listMilestones, listProjects } from '../projects/api';
import type { Cycle, Milestone, ProjectSummary } from '../projects/types';
import {
  addDependency,
  deleteIssue,
  getIssue,
  getIssueByIdentifier,
  issueChannel,
  listActivity,
  listChildren,
  listDependencies,
  listStatuses,
  movePreview,
  removeDependency as removeDependencyApi,
} from './api';
import { IssueProperties } from './IssueProperties';
import { MoveProjectDialog } from './MoveProjectDialog';
import { applyIssueDetailFrame } from './realtime';
import type {
  ActivityEntry,
  DependencyEntry,
  DependencyType,
  IssueDetail,
  IssueSummary,
  IssueStatusRef,
  MovePreview,
  StateCategory,
} from './types';
import { useSaveIndicator } from './useSaveIndicator';
import type { SavePhase } from './useSaveIndicator';
import './issues.css';

/** 状态类别 → 徽标 tone(图标 + 文案承载语义,颜色非唯一信号,§7.2)。 */
export function categoryTone(category: StateCategory): BadgeTone {
  switch (category) {
    case 'todo':
      return 'info';
    case 'in_progress':
      return 'accent';
    case 'in_review':
      return 'warning';
    case 'blocked':
      return 'danger';
    case 'done':
      return 'success';
    case 'backlog':
    case 'cancelled':
      return 'neutral';
  }
}

interface AddDependencyFormProps {
  readonly issueId: string;
  readonly workspaceId: string;
  readonly onAdded: (entry: DependencyEntry) => void;
}

/** 建立依赖(§4.2/§4.3:搜索标识符/UUID 选目标 + 选类型;成环就地报错,不创建)。 */
function AddDependencyForm(props: AddDependencyFormProps): React.JSX.Element {
  const t = useT();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [target, setTarget] = useState('');
  const [depType, setDepType] = useState<DependencyType>('blocked_by');
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const submit = useCallback(async () => {
    const value = target.trim();
    if (value === '') return;
    setIsBusy(true);
    setError(null);
    try {
      // 目标是人类可读编号(如 WEB-12)时先解析为 UUID;否则按 UUID 解析
      let dependsOnId = value;
      const uuidRe = /^[0-9a-fA-F-]{36}$/;
      if (!uuidRe.test(value)) {
        const resolved = await getIssueByIdentifier(client, props.workspaceId, value);
        dependsOnId = resolved.id;
      }
      const entry = await addDependency(client, props.issueId, {
        depends_on_id: dependsOnId,
        type: depType,
      });
      props.onAdded(entry);
      setTarget('');
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      setError(t(key));
    } finally {
      setIsBusy(false);
    }
  }, [client, props, target, depType, t]);

  return (
    <div className="mesh-issues__dep-add">
      <input
        value={target}
        onChange={(event) => setTarget(event.target.value)}
        placeholder={t('issues.deps.targetPlaceholder')}
        aria-label={t('issues.deps.targetPlaceholder')}
        data-testid="dep-target-input"
      />
      <Select
        label={t('issues.deps.typeLabel')}
        value={depType}
        data-testid="dep-type-select"
        onChange={(event) => setDepType(event.target.value as DependencyType)}
      >
        {(
          [
            ['blocked_by', t('issues.deps.type.blocked_by')],
            ['blocks', t('issues.deps.type.blocks')],
            ['relates_to', t('issues.deps.type.relates_to')],
            ['duplicates', t('issues.deps.type.duplicates')],
          ] as const
        ).map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </Select>
      <Button size="sm" disabled={isBusy || target.trim() === ''} onClick={() => void submit()}>
        {t('issues.deps.add')}
      </Button>
      {error !== null ? (
        <p className="mesh-issues__dep-error" role="alert" data-testid="dep-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** 从名册解析当前用户的成员引用(人类档案 profile.id 即 users.id,邮箱兜底)。 */
function resolveCurrentMember(
  members: readonly MemberSummary[],
  userId: string,
  userEmail: string,
): CommentMemberRef | null {
  for (const member of members) {
    if (member.member_type !== 'human') continue;
    const profile = member.profile as HumanProfile | null;
    if (profile !== null && (profile.id === userId || profile.email === userEmail)) {
      return { id: member.id, member_type: member.member_type, name: member.display_name };
    }
  }
  return null;
}

/** 名册 → @提及候选(人/agent 混排;member_type 供 UI 区分与触发预览)。 */
function toMentionCandidates(members: readonly MemberSummary[]): MentionCandidate[] {
  return members
    .filter((member) => member.status === 'active')
    .map((member) => ({ id: member.id, name: member.display_name, member_type: member.member_type }));
}

interface ActivityListProps {
  readonly activity: readonly ActivityEntry[];
}

/** 原始活动流(评论区在另一 Tab 内交织呈现系统活动,§9.5)。 */
function ActivityList(props: ActivityListProps): React.JSX.Element {
  const t = useT();
  if (props.activity.length === 0) {
    return <p className="mesh-issues-detail__empty">{t('issues.detail.noActivity')}</p>;
  }
  return (
    <ul className="mesh-issues-detail__activity" data-testid="issue-detail-activity">
      {props.activity.map((entry, index) => (
        <li key={entry.id ?? `act-${index}`}>
          <strong>{entry.actor !== null ? entry.actor.name : t('issues.systemActor')}</strong>
          {t('issues.activity.changed', { field: entry.field })}
          <time>{new Date(entry.created_at).toLocaleString()}</time>
        </li>
      ))}
    </ul>
  );
}

/** 保存态弱提示文案(idle 为空串;role=status 由读屏适时朗读,§10.2)。 */
function saveIndicatorText(phase: SavePhase, t: (key: string) => string): string {
  if (phase === 'saving') return t('issues.saving');
  if (phase === 'saved') return t('issues.savedAt');
  if (phase === 'conflict') return t('issues.conflictNotice');
  return '';
}

export function IssueDetailPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const { issueId } = useParams<{ issueId: string }>();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const realtime = useRealtimeContext();
  const locale = useSettingsStore((state) => state.preferences.locale) ?? 'en';
  const indicator = useSaveIndicator();

  // issue 详情上下文组(§4.3 S11):['global','board','issue'] —— issue 特异性
  // 最高,同键仲裁胜出(详情抽屉叠于看板之上时 board 组共存而不冲突)。
  usePageContext('board', 'issue');

  useEffect(() => {
    const registry = useShortcutRegistry.getState();
    const focusField = (testid: string) => () => {
      document.querySelector<HTMLElement>(`[data-testid="${testid}"]`)?.focus();
    };
    return registry.registerShortcuts([
      { id: 'issue.edit', combo: 'e', label: t('shortcuts.issueEdit'), group: 'issue', run: focusField('issue-detail-title') },
      { id: 'issue.status', combo: 's', label: t('shortcuts.issueStatus'), group: 'issue', run: focusField('issue-detail-status') },
      { id: 'issue.assignee', combo: 'a', label: t('shortcuts.issueAssignee'), group: 'issue', run: focusField('issue-detail-assignee') },
      { id: 'issue.priority', combo: 'p', label: t('shortcuts.issuePriority'), group: 'issue', run: focusField('issue-detail-priority') },
      // L:打开标签选择器(评审 P5,label-property.md 既有搜索输入即选择入口)。
      { id: 'issue.labels', combo: 'l', label: t('shortcuts.issueLabels'), group: 'issue', run: focusField('issue-label-search') },
      { id: 'issue.milestone', combo: 'm', label: t('shortcuts.issueMilestone'), group: 'issue', run: focusField('issue-detail-milestone') },
      {
        id: 'issue.submit.comment',
        combo: 'mod+enter',
        label: t('shortcuts.issueSubmitComment'),
        group: 'issue',
        run: () => {
          document.querySelector<HTMLButtonElement>('[data-testid="composer-submit"]')?.click();
        },
      },
      { id: 'issue.close', combo: 'esc', label: t('shortcuts.issueClose'), group: 'issue', run: () => navigate(-1) },
    ]);
  }, [t, navigate]);

  const [issue, setIssue] = useState<IssueDetail | null>(null);
  const [statuses, setStatuses] = useState<IssueStatusRef[]>([]);
  const [members, setMembers] = useState<MemberSummary[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [milestones, setMilestones] = useState<Milestone[]>([]);
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [children, setChildren] = useState<IssueSummary[]>([]);
  const [dependencies, setDependencies] = useState<DependencyEntry[]>([]);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [currentMember, setCurrentMember] = useState<CommentMemberRef | null>(null);
  const [titleDraft, setTitleDraft] = useState('');
  const [descriptionDraft, setDescriptionDraft] = useState('');
  const [movePreviewData, setMovePreviewData] = useState<MovePreview | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('comments');

  // t 的函数身份每次渲染都变;经 ref 读取,避免加载副作用反复重建(重叠请求竞态)。
  const tRef = useRef(t);
  tRef.current = t;

  const mutation = useOptimisticMutation<IssueDetail>({
    client,
    path: `/api/v1/issues/${issueId ?? ''}`,
    getServerVersion: (current) => current.updated_at,
    onConflict: async (server) => {
      // 收敛到服务端最新写(T9:不丢更新,冲突弱提示 + toast)
      setIssue(server);
      setTitleDraft(server.title);
      setDescriptionDraft(server.description ?? '');
      return server;
    },
  });

  useEffect(() => {
    if (issueId === undefined) return;
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    void (async () => {
      try {
        const detail = await getIssue(client, issueId);
        const [defs, kids, deps, acts, roster, projectPage, cyclePage, me] = await Promise.all([
          listStatuses(client, detail.workspace_id, detail.project_id ?? undefined),
          listChildren(client, issueId),
          listDependencies(client, issueId),
          listActivity(client, issueId),
          listMembers(client, detail.workspace_id, { limit: 100 }),
          listProjects(client, detail.workspace_id, { limit: 100 }),
          listCycles(client, detail.workspace_id, { limit: 100 }),
          fetchMe(client),
        ]);
        const milestonePage =
          detail.project_id !== null
            ? await listMilestones(client, detail.project_id, { limit: 100 })
            : { data: [] };
        if (cancelled) return;
        setIssue(detail);
        setTitleDraft(detail.title);
        setDescriptionDraft(detail.description ?? '');
        setStatuses([...defs]);
        setChildren([...kids.data]);
        setDependencies([...deps]);
        setActivity([...acts.data]);
        setMembers(roster.data);
        setProjects([...projectPage.data]);
        setMilestones([...milestonePage.data]);
        setCycles([...cyclePage.data]);
        setCurrentMember(resolveCurrentMember(roster.data, me.user.id, me.user.email));
      } catch (err: unknown) {
        if (cancelled) return;
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setError(tRef.current(key));
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, issueId, reloadKey]);

  // 详情级实时合并(§3.6:issue:{id} 频道)
  const issueKey = issue !== null ? issue.id : null;
  useEffect(() => {
    if (issueKey === null || realtime === null) return;
    const channel = issueChannel(issueKey);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      setIssue((prev) => (prev === null ? prev : applyIssueDetailFrame(prev, frame)));
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, issueKey]);

  const patchAndToast = useCallback(
    async (changes: Partial<IssueDetail>) => {
      if (issue === null) return;
      // 乐观更新:让受控 <select>/输入立即反映所选值(异步间隙不悬停目标值,§4.4/§5.2)。
      const snapshot = issue;
      setIssue({ ...issue, ...changes });
      indicator.begin();
      try {
        const { conflicted } = await mutation.mutate(snapshot, changes);
        if (conflicted) indicator.conflict();
        else indicator.succeed();
        toast.addToast(t(conflicted ? 'issues.conflictToast' : 'issues.savedToast'), {
          tone: conflicted ? 'warn' : 'success',
          closeLabel: t('common.close'),
        });
        // 成功:重取以收敛 version / children_progress / activity 等服务端派生数据。
        setReloadKey((k) => k + 1);
      } catch (err: unknown) {
        // 被服务端拒绝(如严格模式 409 invalid_status_transition):就地回滚到快照,
        // select 回落原值、不保留被禁目标值,且不触发整页 reload / 骨架闪烁(§4.4/§5.2)。
        setIssue(snapshot);
        setTitleDraft(snapshot.title);
        setDescriptionDraft(snapshot.description ?? '');
        indicator.reset();
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [issue, mutation, toast, t, indicator],
  );

  const saveTitle = useCallback(async () => {
    if (issue === null || titleDraft.trim() === '' || titleDraft === issue.title) return;
    await patchAndToast({ title: titleDraft.trim(), version: issue.version });
  }, [issue, titleDraft, patchAndToast]);

  const saveDescription = useCallback(async () => {
    if (issue === null) return;
    const next = descriptionDraft.trim() === '' ? null : descriptionDraft;
    if (next === issue.description) return;
    await patchAndToast({ description: next, version: issue.version });
  }, [issue, descriptionDraft, patchAndToast]);

  const remove = useCallback(async () => {
    if (issue === null) return;
    try {
      await deleteIssue(client, issue.id);
      navigate('/issues');
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    }
  }, [client, issue, navigate, toast, t]);

  const removeDependency = useCallback(
    async (entry: DependencyEntry) => {
      if (issue === null) return;
      // 乐观移除 + 失败回滚(§4.3)
      setDependencies((prev) => prev.filter((dep) => dep.id !== entry.id));
      try {
        await removeDependencyApi(client, issue.id, entry.id);
      } catch (err: unknown) {
        setDependencies((prev) => [...prev, entry]);
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [client, issue, toast, t],
  );

  // 跨项目迁移第一步:拉取预览,弹确认对话框(§4.3/§3.8)
  const requestMove = useCallback(
    async (targetProjectId: string | null) => {
      if (issue === null) return;
      if (targetProjectId === issue.project_id) return;
      try {
        const preview = await movePreview(client, issue.id, targetProjectId);
        setMovePreviewData(preview);
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
        setReloadKey((k) => k + 1);
      }
    },
    [client, issue, toast, t],
  );

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
  if (isLoading || issue === null) {
    return <Skeleton loadingLabel={t('common.loading')} />;
  }

  // F7:进度以服务端 children_progress 为准;畸形信封按 unknown 收窄后回退 0(不白屏)。
  const progress = issue.children_progress as { done?: unknown; total?: unknown } | null | undefined;
  const doneChildren = typeof progress?.done === 'number' ? progress.done : 0;
  const totalChildren = typeof progress?.total === 'number' ? progress.total : 0;

  const header = (
    <header className="mesh-issues-detail__head">
      <span className="mesh-issues-detail__identifier" data-testid="issue-detail-identifier">
        {issue.identifier}
      </span>
      <input
        className="mesh-issues-detail__title"
        value={titleDraft}
        onChange={(event) => setTitleDraft(event.target.value)}
        onBlur={() => void saveTitle()}
        onKeyDown={(event) => {
          if (event.key === 'Enter') void saveTitle();
          if (event.key === 'Escape') setTitleDraft(issue.title);
        }}
        aria-label={t('issues.detail.title')}
        data-testid="issue-detail-title"
      />
      <span className="mesh-issues-detail__version" data-testid="issue-detail-version">
        v{issue.version}
      </span>
      <span
        className="mesh-issues-detail__save-state"
        data-phase={indicator.phase}
        data-testid="issue-save-indicator"
        role="status"
      >
        {saveIndicatorText(indicator.phase, t)}
      </span>
      <Menu
        align="end"
        triggerLabel={t('issues.actions')}
        trigger={<Icon name="more-horizontal" size={16} />}
        entries={[
          {
            key: 'delete',
            label: t('issues.detail.delete'),
            icon: 'trash',
            danger: true,
            onSelect: () => setDeleteConfirmOpen(true),
          },
        ]}
      />
    </header>
  );

  const summaryChips = (
    <div className="mesh-issues-detail__chips">
      <span className="mesh-issues-detail__chip" data-testid="issue-chip-status">
        <Badge tone={categoryTone(issue.state_category)}>
          {issue.status !== null ? issue.status.name : t(`issues.category.${issue.state_category}`)}
        </Badge>
      </span>
      <span className="mesh-issues-detail__chip" data-testid="issue-chip-priority">
        <Icon name="flag" size={16} />
        {t(`issues.priority.${issue.priority}`)}
      </span>
      <span className="mesh-issues-detail__chip" data-testid="issue-chip-assignee">
        {issue.assignee !== null ? (
          <>
            <Avatar name={issue.assignee.name} kind={issue.assignee.member_type} size={20} />
            {issue.assignee.name}
          </>
        ) : (
          t('issues.unassigned')
        )}
      </span>
      <span className="mesh-issues-detail__chip" data-testid="issue-chip-due">
        <Icon name="calendar" size={16} />
        {issue.due_date !== null ? issue.due_date : t('issues.detail.noDue')}
      </span>
    </div>
  );

  const main = (
    <section className="mesh-issues-detail__main">
      <h2>{t('issues.detail.description')}</h2>
      <textarea
        className="mesh-issues-detail__description"
        value={descriptionDraft}
        onChange={(event) => setDescriptionDraft(event.target.value)}
        onBlur={() => void saveDescription()}
        placeholder={t('issues.detail.noDescription')}
        aria-label={t('issues.detail.description')}
        data-testid="issue-detail-description"
        rows={4}
      />

      <h2>
        {t('issues.detail.children')}（{doneChildren}/{totalChildren}）
      </h2>
      {children.length === 0 ? (
        <p className="mesh-issues-detail__empty">{t('issues.detail.noChildren')}</p>
      ) : (
        <ul className="mesh-issues-detail__children" data-testid="issue-detail-children">
          {children.map((child) => (
            <li key={child.id}>
              <Link to={`/issues/${child.id}`}>
                {child.identifier} · {child.title}
              </Link>
              <span>{t(`issues.category.${child.state_category}`)}</span>
            </li>
          ))}
        </ul>
      )}

      <h2>{t('issues.detail.dependencies')}</h2>
      {dependencies.length === 0 ? (
        <p className="mesh-issues-detail__empty">{t('issues.detail.noDependencies')}</p>
      ) : (
        <ul className="mesh-issues-detail__deps" data-testid="issue-detail-deps">
          {dependencies.map((dep) => (
            <li key={dep.id}>
              <span data-testid={`dep-type-${dep.id}`}>{t(`issues.deps.type.${dep.type}`)}</span>
              <Link to={`/issues/${dep.depends_on_id}`} data-testid={`dep-link-${dep.id}`}>
                {dep.depends_on_identifier ?? dep.depends_on_id.slice(0, 8)}
              </Link>
              <Button size="sm" variant="ghost" onClick={() => void removeDependency(dep)}>
                {t('issues.deps.remove')}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <AddDependencyForm
        issueId={issue.id}
        workspaceId={issue.workspace_id}
        onAdded={(entry) => setDependencies((prev) => [...prev, entry])}
      />

      <AttachmentPanel workspaceId={issue.workspace_id} issueId={issue.id} />

      {/* 讨论/活动切换(§3.2:活动/评论可切换;默认评论 —— 其已交织系统活动,§9.5)。
          仅渲染活动 Tab 内容;评论草稿经 localStorage 持久化,切换不丢(useCommentDraft)。 */}
      <Tabs
        label={t('issues.tabsLabel')}
        value={activeTab}
        onChange={setActiveTab}
        items={[
          {
            value: 'comments',
            label: t('issues.tabComments'),
            content: (
              <CommentsPanel
                issueId={issue.id}
                workspaceId={issue.workspace_id}
                locale={locale}
                candidates={toMentionCandidates(members)}
                currentMember={currentMember}
              />
            ),
          },
          {
            value: 'activity',
            label: t('issues.tabActivity'),
            content: <ActivityList activity={activity} />,
          },
        ]}
      />
    </section>
  );

  return (
    <div className="mesh-issues-detail" data-testid="issue-detail">
      <DetailLayout
        header={header}
        summaryChips={summaryChips}
        main={main}
        aside={
          <IssueProperties
            issue={issue}
            statuses={statuses}
            members={members}
            projects={projects}
            milestones={milestones}
            cycles={cycles}
            client={client}
            realtime={realtime}
            reloadKey={reloadKey}
            onPatch={(changes) => void patchAndToast(changes)}
            onRequestMove={(target) => void requestMove(target)}
            onIssueChanged={() => setReloadKey((k) => k + 1)}
          />
        }
        asideTitle={t('issues.propertiesTitle')}
        asideTriggerLabel={t('issues.openProperties')}
        closeLabel={t('common.close')}
      />

      {movePreviewData !== null ? (
        <MoveProjectDialog
          preview={movePreviewData}
          targetProjectName={
            movePreviewData.target_project_id === null
              ? t('issues.detail.inbox')
              : (projects.find((project) => project.id === movePreviewData.target_project_id)
                  ?.name ?? movePreviewData.target_project_id)
          }
          version={issue.version}
          onCancel={() => setMovePreviewData(null)}
          onDone={() => {
            setMovePreviewData(null);
            setReloadKey((k) => k + 1);
          }}
          onPreviewRefresh={setMovePreviewData}
        />
      ) : null}

      <Dialog
        open={deleteConfirmOpen}
        onClose={() => setDeleteConfirmOpen(false)}
        title={t('issues.deleteConfirmTitle')}
        closeLabel={t('common.close')}
      >
        <p className="mesh-text-body-sm">{t('issues.deleteConfirmBody', { identifier: issue.identifier })}</p>
        <div className="mesh-issues__confirm-actions">
          <Button
            variant="danger"
            data-testid="issue-delete-confirm"
            onClick={() => {
              setDeleteConfirmOpen(false);
              void remove();
            }}
          >
            {t('issues.detail.delete')}
          </Button>
          <Button variant="ghost" onClick={() => setDeleteConfirmOpen(false)}>
            {t('common.cancel')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
