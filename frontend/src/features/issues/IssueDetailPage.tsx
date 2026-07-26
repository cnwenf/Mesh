/**
 * Issue 详情页(issue.md §4.1/§4.2/§4.3):
 * 头部(编号 / 可编辑标题 / 状态选择器按 category 分组 / 删除)+
 * 属性侧栏(优先级 / 负责人(人与 agent 同列)/ 截止日)+
 * 子 issue 区(进度 3/5 + 列表)+ 依赖区(blocked_by,成环就地报错)+ 活动流。
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
import {
  addDependency,
  deleteIssue,
  getIssue,
  issueChannel,
  listActivity,
  listChildren,
  listDependencies,
  listStatuses,
  removeDependency as removeDependencyApi,
} from './api';
import { applyIssueDetailFrame } from './realtime';
import type {
  ActivityEntry,
  DependencyEntry,
  IssueDetail,
  IssuePriority,
  IssueStatusRef,
  IssueSummary,
} from './types';
import { PRIORITY_ORDER, STATE_CATEGORY_ORDER } from './types';
import './issues.css';

interface AddDependencyFormProps {
  readonly issueId: string;
  readonly onAdded: (entry: DependencyEntry) => void;
}

/** 建立依赖(§4.3:输入目标 + 类型;成环就地报错,不创建)。 */
function AddDependencyForm(props: AddDependencyFormProps): React.JSX.Element {
  const t = useT();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [target, setTarget] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const submit = useCallback(async () => {
    if (target.trim() === '') return;
    setIsBusy(true);
    setError(null);
    try {
      const entry = await addDependency(client, props.issueId, {
        depends_on_id: target.trim(),
        type: 'blocked_by',
      });
      props.onAdded(entry);
      setTarget('');
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      setError(t(key));
    } finally {
      setIsBusy(false);
    }
  }, [client, props, target, t]);

  return (
    <div className="mesh-issues__dep-add">
      <input
        value={target}
        onChange={(event) => setTarget(event.target.value)}
        placeholder={t('issues.deps.targetPlaceholder')}
        aria-label={t('issues.deps.targetPlaceholder')}
        data-testid="dep-target-input"
      />
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
  const [children, setChildren] = useState<IssueSummary[]>([]);
  const [dependencies, setDependencies] = useState<DependencyEntry[]>([]);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [titleDraft, setTitleDraft] = useState('');
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
        const [defs, kids, deps, acts, roster] = await Promise.all([
          listStatuses(client, detail.workspace_id, detail.project_id ?? undefined),
          listChildren(client, issueId),
          listDependencies(client, issueId),
          listActivity(client, issueId),
          listMembers(client, detail.workspace_id, { limit: 100 }),
        ]);
        if (cancelled) return;
        setIssue(detail);
        setTitleDraft(detail.title);
        setStatuses([...defs]);
        setChildren([...kids.data]);
        setDependencies([...deps]);
        setActivity([...acts.data]);
        setMembers(roster.data);
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
      const { conflicted } = await mutation.mutate(issue, changes);
      toast.addToast(t(conflicted ? 'issues.conflictToast' : 'issues.savedToast'), {
        tone: conflicted ? 'warn' : 'success',
        closeLabel: t('common.close'),
      });
      // 重取以刷新 version/status 等服务端派生字段(冲突时 onConflict 已收敛)
      setReloadKey((k) => k + 1);
    },
    [issue, mutation, toast, t],
  );

  const saveTitle = useCallback(async () => {
    if (issue === null || titleDraft.trim() === '' || titleDraft === issue.title) return;
    await patchAndToast({ title: titleDraft.trim(), version: issue.version });
  }, [issue, titleDraft, patchAndToast]);

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
  const doneChildren = children.filter((c) => c.state_category === 'done').length;

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
          <p className="mesh-issues-detail__description" data-testid="issue-detail-description">
            {issue.description != null ? issue.description : t('issues.detail.noDescription')}
          </p>

          <h2>
            {t('issues.detail.children')}（{doneChildren}/{children.length}）
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
                  <code>{dep.depends_on_id}</code>
                  <Button size="sm" variant="ghost" onClick={() => void removeDependency(dep)}>
                    {t('issues.deps.remove')}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <AddDependencyForm
            issueId={issue.id}
            onAdded={(entry) => setDependencies((prev) => [...prev, entry])}
          />

          <h2>{t('issues.detail.activity')}</h2>
          {activity.length === 0 ? (
            <p className="mesh-issues-detail__empty">{t('issues.detail.noActivity')}</p>
          ) : (
            <ul className="mesh-issues-detail__activity" data-testid="issue-detail-activity">
              {activity.map((entry) => (
                <li key={entry.id}>
                  <strong>{entry.actor !== null ? entry.actor.name : t('issues.systemActor')}</strong>
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
          <p className="mesh-issues-detail__meta">
            {t('issues.detail.reporter')}:{' '}
            {issue.reporter != null ? issue.reporter.name : t('issues.unassigned')}
          </p>
          {issue.project != null ? (
            <p className="mesh-issues-detail__meta">
              {t('issues.detail.project')}: {issue.project.name}（{issue.project.key}）
            </p>
          ) : (
            <p className="mesh-issues-detail__meta">{t('issues.detail.inbox')}</p>
          )}
        </aside>
      </div>
    </div>
  );
}
