/**
 * Issue 详情页(issue.md §4.1/§4.2/§4.3):
 * 头部(编号 / 可编辑标题 / 状态选择器按 category 分组 / 删除)+
 * 主体(可编辑描述、子 issue 区(进度 3/5)、依赖区(成环就地报错)、活动流)+
 * 属性侧栏(每字段点击即编辑,§4.2:状态/优先级/负责人/估算/起止日/项目/里程碑/周期)+
 * 跨项目迁移两步式(§4.3:改项目 → 预览映射/清除清单 → 确认单事务迁移)。
 * 乐观更新 + version 冲突收敛(§3.4/T9:useOptimisticMutation,If-Match: updated_at)。
 * 实时经 issue:{id} 频道按 id 合并(§3.6/§6.7)。
 * 状态渲染序:错误态(可重试)→ 骨架 → 内容。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken, useOptimisticMutation } from '../../api';
import { Button, ErrorState, Select, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { listMembers } from '../members/api';
import type { MemberSummary } from '../members/types';
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
  moveIssue,
  movePreview,
  removeDependency as removeDependencyApi,
} from './api';
import { applyIssueDetailFrame } from './realtime';
import type {
  ActivityEntry,
  DependencyEntry,
  DependencyType,
  IssueDetail,
  IssuePriority,
  IssueStatusRef,
  IssueSummary,
  MovePreview,
  MovePreviewField,
} from './types';
import { PRIORITY_ORDER, STATE_CATEGORY_ORDER, isMovePreview } from './types';
import './issues.css';

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

/**
 * 迁移预览字段技术键 → 可读文案映射键(LOW-2):后端 `field` 为 snake_case 技术键
 * (issue.md §3.8),UI 以本地化字段名呈现;未知键回退原始值(不中断渲染)。
 */
const MOVE_FIELD_LABEL_KEYS: Readonly<Record<string, string>> = {
  status: 'issues.move.field.status',
  milestone_id: 'issues.move.field.milestone_id',
  cycle_id: 'issues.move.field.cycle_id',
  labels: 'issues.move.field.labels',
  custom_field_values: 'issues.move.field.custom_field_values',
};

/**
 * 迁移预览 reason → 可读文案映射键(LOW-2):reason 取自 issue.md §3.8 契约词汇
 * (含后端模块未就绪占位码);未知 reason 回退原始值(后端新增词汇前不中断渲染)。
 */
const MOVE_REASON_LABEL_KEYS: Readonly<Record<string, string>> = {
  '项目私有 status → 目标项目同 category 默认 status': 'issues.move.reason.statusMapped',
  项目私有里程碑: 'issues.move.reason.projectMilestone',
  项目绑定的周期: 'issues.move.reason.projectCycle',
  项目级标签: 'issues.move.reason.projectLabels',
  项目级自定义字段值: 'issues.move.reason.projectCustomFields',
  label_module_pending: 'issues.move.reason.labelModulePending',
  custom_field_module_pending: 'issues.move.reason.customFieldModulePending',
};

function moveFieldLabel(t: (key: string) => string, field: string): string {
  const key = MOVE_FIELD_LABEL_KEYS[field];
  return key !== undefined ? t(key) : field;
}

function moveReasonLabel(t: (key: string) => string, reason: string): string {
  const key = MOVE_REASON_LABEL_KEYS[reason];
  return key !== undefined ? t(key) : reason;
}

interface MoveDialogProps {
  readonly preview: MovePreview;
  /** 解析后的目标项目显示名(null 目标 = 工作区收件箱);对话框须标明迁移去向(§4.3/§3.8)。 */
  readonly targetProjectName: string;
  readonly version: number;
  readonly onCancel: () => void;
  readonly onDone: () => void;
  /** LOW-3:422 move_confirmation_required 携最新预览时回写父级(保持对话框,重渲染预览)。 */
  readonly onPreviewRefresh: (preview: MovePreview) => void;
}

/** 跨项目迁移预览确认对话框(§4.3/§3.8 两步式契约第二步)。 */
function MoveProjectDialog(props: MoveDialogProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [isBusy, setIsBusy] = useState(false);
  const { preview } = props;

  const confirm = useCallback(async () => {
    setIsBusy(true);
    try {
      await moveIssue(client, preview.issue_id, {
        target_project_id: preview.target_project_id,
        confirm: true,
        version: props.version,
      });
      toast.addToast(t('issues.move.success'), { tone: 'success', closeLabel: t('common.close') });
      props.onDone();
    } catch (err: unknown) {
      // LOW-3:预览过期 → 422 move_confirmation_required(契约:details.preview 携最新预览)。
      // 以最新预览重渲染并保持对话框,不降级为通用 toast + 关闭(issue.md §3.8/README §6.14)。
      if (err instanceof MeshApiError && err.code === 'move_confirmation_required') {
        const freshPreview = err.details?.preview;
        if (isMovePreview(freshPreview)) {
          props.onPreviewRefresh(freshPreview);
          return;
        }
      }
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      props.onCancel();
    } finally {
      setIsBusy(false);
    }
  }, [client, preview, props, toast, t]);

  return (
    <div className="mesh-issues__move-overlay" data-testid="move-dialog">
      <div className="mesh-issues__move-dialog" role="dialog" aria-label={t('issues.move.title')}>
        <h3>{t('issues.move.title')}</h3>
        <p className="mesh-issues__move-identifier">{preview.identifier}</p>
        <p className="mesh-issues__move-target" data-testid="move-target">
          {t('issues.move.targetProject', { name: props.targetProjectName })}
        </p>
        {preview.mapped_fields.length > 0 ? (
          <section data-testid="move-mapped">
            <h4>{t('issues.move.mapped')}</h4>
            <ul>
              {preview.mapped_fields.map((field: MovePreviewField) => {
                const from = field.from as { name?: string } | undefined;
                const to = field.to as { name?: string } | undefined;
                return (
                  <li key={field.field}>
                    {moveFieldLabel(t, field.field)}: {from?.name ?? '?'} → {to?.name ?? '?'}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}
        {preview.cleared_fields.length > 0 ? (
          <section data-testid="move-cleared">
            <h4>{t('issues.move.cleared')}</h4>
            <ul>
              {preview.cleared_fields.map((field: MovePreviewField) => (
                <li key={field.field}>
                  {moveFieldLabel(t, field.field)}({moveReasonLabel(t, field.reason)})
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <p className="mesh-issues__move-kept">{t('issues.move.keptNote')}</p>
        <div className="mesh-issues__move-actions">
          <Button variant="ghost" onClick={props.onCancel} data-testid="move-cancel">
            {t('issues.move.cancel')}
          </Button>
          <Button onClick={() => void confirm()} disabled={isBusy} data-testid="move-confirm">
            {t('issues.move.confirm')}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function IssueDetailPage(): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const { issueId } = useParams<{ issueId: string }>();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const realtime = useRealtimeContext();

  const [issue, setIssue] = useState<IssueDetail | null>(null);
  const [statuses, setStatuses] = useState<IssueStatusRef[]>([]);
  const [members, setMembers] = useState<MemberSummary[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [milestones, setMilestones] = useState<Milestone[]>([]);
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [children, setChildren] = useState<IssueSummary[]>([]);
  const [dependencies, setDependencies] = useState<DependencyEntry[]>([]);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [titleDraft, setTitleDraft] = useState('');
  const [descriptionDraft, setDescriptionDraft] = useState('');
  const [movePreviewData, setMovePreviewData] = useState<MovePreview | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // t 的函数身份每次渲染都变;经 ref 读取,避免加载副作用反复重建(重叠请求竞态)。
  const tRef = useRef(t);
  tRef.current = t;

  const mutation = useOptimisticMutation<IssueDetail>({
    client,
    path: `/api/v1/issues/${issueId ?? ''}`,
    getServerVersion: (current) => current.updated_at,
    onConflict: async (server) => {
      // 收敛到服务端最新写(T9:不丢更新,冲突 toast 提示)
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
        const [defs, kids, deps, acts, roster, projectPage, cyclePage] = await Promise.all([
          listStatuses(client, detail.workspace_id, detail.project_id ?? undefined),
          listChildren(client, issueId),
          listDependencies(client, issueId),
          listActivity(client, issueId),
          listMembers(client, detail.workspace_id, { limit: 100 }),
          listProjects(client, detail.workspace_id, { limit: 100 }),
          listCycles(client, detail.workspace_id, { limit: 100 }),
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
      // 乐观更新:让受控 <select>/输入立即反映所选值。受控组件只在 re-render 时
      // 才会把显示值收敛回 value 属性;若不在此同步 setState,异步等待间隙里
      // <select> 会悬停在用户所选的(严格模式下可能被禁的)目标值上(§4.4/§5.2)。
      const snapshot = issue;
      setIssue({ ...issue, ...changes });
      try {
        const { conflicted } = await mutation.mutate(snapshot, changes);
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
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [issue, mutation, toast, t],
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

  const groupedStatuses = STATE_CATEGORY_ORDER.map((category) => ({
    category,
    items: statuses.filter((s) => s.category === category),
  })).filter((group) => group.items.length > 0);
  // F7:进度以服务端 children_progress 为准(不受本地分页截断影响)
  const doneChildren = issue.children_progress.done;
  const totalChildren = issue.children_progress.total;

  return (
    <div className="mesh-issues-detail" data-testid="issue-detail">
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
          }}
          aria-label={t('issues.detail.title')}
          data-testid="issue-detail-title"
        />
        <span className="mesh-issues-detail__version" data-testid="issue-detail-version">
          v{issue.version}
        </span>
        <Button variant="danger" size="sm" onClick={() => void remove()}>
          {t('issues.detail.delete')}
        </Button>
      </header>

      <div className="mesh-issues-detail__body">
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
                  <span data-testid={`dep-type-${dep.id}`}>
                    {t(`issues.deps.type.${dep.type}`)}
                  </span>
                  <Link
                    to={`/issues/${dep.depends_on_id}`}
                    data-testid={`dep-link-${dep.id}`}
                  >
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

          <h2>{t('issues.detail.activity')}</h2>
          {activity.length === 0 ? (
            <p className="mesh-issues-detail__empty">{t('issues.detail.noActivity')}</p>
          ) : (
            <ul className="mesh-issues-detail__activity" data-testid="issue-detail-activity">
              {activity.map((entry, index) => (
                <li key={entry.id ?? `act-${index}`}>
                  <strong>{entry.actor != null ? entry.actor.name : t('issues.systemActor')}</strong>
                  {t('issues.activity.changed', { field: entry.field })}
                  <time>{new Date(entry.created_at).toLocaleString()}</time>
                </li>
              ))}
            </ul>
          )}
        </section>

        <aside className="mesh-issues-detail__sidebar" aria-label={t('issues.detail.properties')}>
          <Select
            label={t('issues.columns.status')}
            value={issue.status_id}
            data-testid="issue-detail-status"
            onChange={(event) =>
              void patchAndToast({ status_id: event.target.value, version: issue.version })
            }
          >
            {groupedStatuses.map((group) => (
              <optgroup key={group.category} label={t(`issues.category.${group.category}`)}>
                {group.items.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </Select>
          <Select
            label={t('issues.priority.label')}
            value={issue.priority}
            data-testid="issue-detail-priority"
            onChange={(event) =>
              void patchAndToast({
                priority: event.target.value as IssuePriority,
                version: issue.version,
              })
            }
          >
            {PRIORITY_ORDER.map((p) => (
              <option key={p} value={p}>
                {t(`issues.priority.${p}`)}
              </option>
            ))}
          </Select>
          <Select
            label={t('issues.columns.assignee')}
            value={issue.assignee_id ?? ''}
            data-testid="issue-detail-assignee"
            onChange={(event) => {
              const value = event.target.value;
              void patchAndToast({
                assignee_id: value === '' ? null : value,
                version: issue.version,
              });
            }}
          >
            <option value="">{t('issues.unassigned')}</option>
            {members
              .filter((m) => m.status === 'active')
              .map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                  {m.member_type === 'agent' ? ` (${t('issues.agentBadge')})` : ''}
                </option>
              ))}
          </Select>
          <label className="mesh-issues__field">
            <span>{t('issues.detail.estimate')}</span>
            <input
              type="number"
              min="0"
              step="0.5"
              value={issue.estimate ?? ''}
              onChange={(event) =>
                void patchAndToast({
                  estimate: event.target.value === '' ? null : Number(event.target.value),
                  version: issue.version,
                })
              }
              aria-label={t('issues.detail.estimate')}
              data-testid="issue-detail-estimate"
            />
          </label>
          <Select
            label={t('issues.detail.estimateUnit')}
            value={issue.estimate_unit ?? ''}
            data-testid="issue-detail-estimate-unit"
            onChange={(event) =>
              void patchAndToast({
                estimate_unit: event.target.value === '' ? null : event.target.value,
                version: issue.version,
              } as Partial<IssueDetail>)
            }
          >
            <option value="">{t('issues.detail.noneOption')}</option>
            <option value="points">{t('issues.detail.estimateUnit.points')}</option>
            <option value="hours">{t('issues.detail.estimateUnit.hours')}</option>
          </Select>
          <label className="mesh-issues__field">
            <span>{t('issues.detail.start')}</span>
            <input
              type="date"
              value={issue.start_date ?? ''}
              onChange={(event) =>
                void patchAndToast({
                  start_date: event.target.value === '' ? null : event.target.value,
                  version: issue.version,
                })
              }
              aria-label={t('issues.detail.start')}
              data-testid="issue-detail-start"
            />
          </label>
          <label className="mesh-issues__field">
            <span>{t('issues.columns.due')}</span>
            <input
              type="date"
              value={issue.due_date ?? ''}
              onChange={(event) =>
                void patchAndToast({
                  due_date: event.target.value === '' ? null : event.target.value,
                  version: issue.version,
                })
              }
              aria-label={t('issues.columns.due')}
              data-testid="issue-detail-due"
            />
          </label>
          <Select
            label={t('issues.detail.milestone')}
            value={issue.milestone_id ?? ''}
            data-testid="issue-detail-milestone"
            onChange={(event) =>
              void patchAndToast({
                milestone_id: event.target.value === '' ? null : event.target.value,
                version: issue.version,
              } as Partial<IssueDetail>)
            }
          >
            <option value="">{t('issues.detail.noneOption')}</option>
            {milestones.map((m) => (
              <option key={m.id} value={m.id}>
                {m.title}
              </option>
            ))}
          </Select>
          <Select
            label={t('issues.detail.cycle')}
            value={issue.cycle_id ?? ''}
            data-testid="issue-detail-cycle"
            onChange={(event) =>
              void patchAndToast({
                cycle_id: event.target.value === '' ? null : event.target.value,
                version: issue.version,
              } as Partial<IssueDetail>)
            }
          >
            <option value="">{t('issues.detail.noneOption')}</option>
            {cycles.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
          <Select
            label={t('issues.detail.project')}
            value={issue.project_id ?? ''}
            data-testid="issue-detail-project"
            onChange={(event) =>
              void requestMove(event.target.value === '' ? null : event.target.value)
            }
          >
            <option value="">{t('issues.detail.inbox')}</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}（{p.key}）
              </option>
            ))}
          </Select>
          <p className="mesh-issues-detail__meta">
            {t('issues.detail.reporter')}:{' '}
            {issue.reporter !== null ? issue.reporter.name : t('issues.unassigned')}
          </p>
        </aside>
      </div>

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
    </div>
  );
}
