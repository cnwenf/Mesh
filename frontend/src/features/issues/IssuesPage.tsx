/**
 * Issue 列表页(issue.md §4.1/§4.2/§4.3):
 * 过滤(q / state_category / priority / assignee=我,URL 同源)+
 * 行表格(勾选 / 编号 / 标题 / 状态色条 / 优先级 / 负责人 / 截止日)+
 * 快速创建对话框(连续创建)+ 批量操作工具条(§1.2.5:改优先级/状态/删除,成功失败计数)+
 * 游标 Load more。实时经 workspace:{ws}:issues 频道按 id 增量合并(§3.6/§6.7)。
 * 状态渲染序:无工作区空态 → 错误态(可重试)→ 骨架 → 空态 → 内容。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useIntl } from 'react-intl';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Button, EmptyState, ErrorState, Select, Skeleton, useToast } from '../../design';
import { env } from '../../env';
import { formatDate, useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { activeWorkspace, fetchMe, listMembers } from '../members/api';
import type { MemberSummary, Membership } from '../members/types';
import { listProjects } from '../projects/api';
import type { ProjectSummary } from '../projects/types';
import { bulkIssues, createIssue, listIssues, listStatuses, workspaceIssuesChannel } from './api';
import { applyIssueListFrame } from './realtime';
import type { IssuePriority, IssueSummary, IssueStatusRef, StateCategory } from './types';
import { PRIORITY_ORDER, STATE_CATEGORY_ORDER } from './types';
import './issues.css';

const PAGE_LIMIT = 25;
const ALL = 'all';

/**
 * 截止日本地化(LOW-2,i18n.md §4.4):due_date 为纯日期值(后端 DATE,'YYYY-MM-DD',
 * 无时分语义),渲染锁定 UTC 解析/格式化,避免按展示时区换算时日历日漂移;
 * 仅格式经 Intl 按 locale 呈现(如 en「Aug 15, 2026」/ zh-CN「2026年8月15日」)。
 * 非法值降级回显原值,单条坏数据不中断整页列表。
 */
function formatDueDate(dueDate: string | null, locale: string): string {
  if (dueDate === null) return '';
  try {
    return formatDate(dueDate, { locale, timeZone: 'UTC', dateStyle: 'medium' });
  } catch {
    return dueDate;
  }
}

function matchesFilters(
  issue: IssueSummary,
  category: string,
  priority: string,
  mineOnly: boolean,
  currentMemberId: string | null,
  q: string,
): boolean {
  if (category !== ALL && issue.state_category !== category) return false;
  if (priority !== ALL && issue.priority !== priority) return false;
  if (mineOnly && (currentMemberId === null || issue.assignee_id !== currentMemberId)) return false;
  if (q !== '') {
    // 与服务端 ILIKE 一致:标题或编号包含搜索词(实时帧合并的可见性水位)
    const needle = q.toLowerCase();
    if (
      !issue.title.toLowerCase().includes(needle) &&
      !issue.identifier.toLowerCase().includes(needle)
    ) {
      return false;
    }
  }
  return true;
}

interface QuickCreateProps {
  readonly workspace: Membership | null;
  readonly members: readonly MemberSummary[];
  readonly onCreated: (issue: IssueSummary) => void;
  readonly onClose: () => void;
}

/** 快速创建(§4.3:回车快速创建,支持连续创建;「展开更多」补项目/负责人字段)。 */
function QuickCreateForm(props: QuickCreateProps): React.JSX.Element {
  const t = useT();
  const [title, setTitle] = useState('');
  const [priority, setPriority] = useState<IssuePriority>('none');
  const [projectId, setProjectId] = useState('');
  const [assigneeId, setAssigneeId] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const { workspace, members } = props;

  // 展开更多字段时按需加载项目名册(成员名册由页面下传,§4.2/§4.3)
  useEffect(() => {
    if (!expanded || workspace === null) return;
    let cancelled = false;
    void (async () => {
      const projectPage = await listProjects(client, workspace.workspace_id, { limit: 100 });
      if (cancelled) return;
      setProjects([...projectPage.data]);
    })();
    return () => {
      cancelled = true;
    };
  }, [expanded, workspace, client]);

  const submit = useCallback(
    async (keepOpen: boolean) => {
      if (workspace === null || title.trim() === '') return;
      setIsSaving(true);
      setError(null);
      try {
        const created = await createIssue(client, workspace.workspace_id, {
          title: title.trim(),
          priority,
          project_id: projectId === '' ? undefined : projectId,
          assignee_id: assigneeId === '' ? undefined : assigneeId,
        });
        props.onCreated(created);
        if (keepOpen) {
          setTitle('');
        } else {
          props.onClose();
        }
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setError(t(key));
      } finally {
        setIsSaving(false);
      }
    },
    [client, workspace, title, priority, projectId, assigneeId, props, t],
  );

  return (
    <form
      className="mesh-issues__create"
      data-testid="issue-create-form"
      onSubmit={(event) => {
        event.preventDefault();
        void submit(false);
      }}
    >
      <label className="mesh-issues__field">
        <span>{t('issues.create.title')}</span>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder={t('issues.create.titlePlaceholder')}
          data-testid="issue-create-title"
          autoFocus
        />
      </label>
      <Select
        label={t('issues.priority.label')}
        value={priority}
        onChange={(event) => setPriority(event.target.value as IssuePriority)}
      >
        {PRIORITY_ORDER.map((p) => (
          <option key={p} value={p}>
            {t(`issues.priority.${p}`)}
          </option>
        ))}
      </Select>
      <Button
        type="button"
        variant="ghost"
        onClick={() => setExpanded((v) => !v)}
        data-testid="issue-create-expand"
      >
        {expanded ? t('issues.create.collapse') : t('issues.create.expand')}
      </Button>
      {expanded ? (
        <>
          <Select
            label={t('issues.create.project')}
            value={projectId}
            data-testid="issue-create-project"
            onChange={(event) => setProjectId(event.target.value)}
          >
            <option value="">{t('issues.detail.inbox')}</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}（{p.key}）
              </option>
            ))}
          </Select>
          <Select
            label={t('issues.create.assignee')}
            value={assigneeId}
            data-testid="issue-create-assignee"
            onChange={(event) => setAssigneeId(event.target.value)}
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
        </>
      ) : null}
      {error !== null ? (
        <p className="mesh-issues__create-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="mesh-issues__create-actions">
        <Button type="submit" disabled={isSaving || title.trim() === ''}>
          {t('issues.create.submit')}
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={isSaving || title.trim() === ''}
          onClick={() => void submit(true)}
        >
          {t('issues.create.submitMore')}
        </Button>
        <Button type="button" variant="ghost" onClick={props.onClose}>
          {t('common.cancel')}
        </Button>
      </div>
    </form>
  );
}

interface BulkBarProps {
  readonly selected: readonly string[];
  readonly statuses: readonly IssueStatusRef[];
  readonly onDone: (summary: { succeeded: number; failed: number }) => void;
  readonly onClear: () => void;
}

/** 批量操作工具条(§4.2 浮出底栏;§5.5 成功/失败计数)。 */
function BulkBar(props: BulkBarProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [isBusy, setIsBusy] = useState(false);

  const run = useCallback(
    async (body: { changes?: { priority?: IssuePriority; status_id?: string }; delete?: boolean }) => {
      setIsBusy(true);
      try {
        const result = await bulkIssues(client, { issue_ids: props.selected, ...body });
        const summary = { succeeded: result.succeeded, failed: result.failed };
        props.onDone(summary);
        toast.addToast(t('issues.bulk.result', summary), {
          tone: summary.failed > 0 ? 'warn' : 'success',
          closeLabel: t('common.close'),
        });
      } catch (err: unknown) {
        if (err instanceof MeshApiError && err.code === 'bulk_partial_failure') {
          const details = err.details as
            | {
                succeeded?: number;
                failed?: number;
                errors?: { issue_id: string; code: string; message: string }[];
              }
            | undefined;
          const summary = {
            succeeded: details?.succeeded ?? 0,
            failed: details?.failed ?? props.selected.length,
          };
          props.onDone(summary);
          // F4:逐条失败原因可定位(§5.5)
          const perItem = (details?.errors ?? [])
            .slice(0, 5)
            .map((e) => `${e.issue_id.slice(0, 8)}: ${e.code}`)
            .join('; ');
          toast.addToast(`${t('issues.bulk.result', summary)}${perItem ? ` — ${perItem}` : ''}`, {
            tone: 'warn',
            closeLabel: t('common.close'),
          });
          return;
        }
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      } finally {
        setIsBusy(false);
      }
    },
    [client, props, t, toast],
  );

  return (
    <div className="mesh-issues__bulkbar" data-testid="issue-bulkbar" role="toolbar">
      <span>{t('issues.bulk.selected', { count: props.selected.length })}</span>
      <Select
        label={t('issues.bulk.setPriority')}
        value=""
        data-testid="bulk-priority"
        onChange={(event) => {
          const value = event.target.value;
          if (value !== '') void run({ changes: { priority: value as IssuePriority } });
        }}
      >
        <option value="">{t('issues.bulk.setPriority')}</option>
        {PRIORITY_ORDER.map((p) => (
          <option key={p} value={p}>
            {t(`issues.priority.${p}`)}
          </option>
        ))}
      </Select>
      <Select
        label={t('issues.bulk.setStatus')}
        value=""
        data-testid="bulk-status"
        onChange={(event) => {
          const value = event.target.value;
          if (value !== '') void run({ changes: { status_id: value } });
        }}
      >
        <option value="">{t('issues.bulk.setStatus')}</option>
        {props.statuses.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </Select>
      <Button variant="danger" disabled={isBusy} onClick={() => void run({ delete: true })}>
        {t('issues.bulk.delete')}
      </Button>
      <Button variant="ghost" onClick={props.onClear}>
        {t('issues.bulk.clear')}
      </Button>
    </div>
  );
}

export function IssuesPage(): React.JSX.Element {
  const t = useT();
  const intl = useIntl();
  const toast = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const realtime = useRealtimeContext();

  const [searchParams, setSearchParams] = useSearchParams();
  const qFilter = searchParams.get('q') ?? '';
  const categoryFilter = searchParams.get('category') ?? ALL;
  const priorityFilter = searchParams.get('priority') ?? ALL;
  const mineOnly = searchParams.get('mine') === 'true';
  // F9:本地搜索输入 300ms 防抖后写 URL(避免逐键重拉/重订阅)
  const [qInput, setQInput] = useState(qFilter);
  useEffect(() => setQInput(qFilter), [qFilter]);
  useEffect(() => {
    if (qInput === qFilter) return;
    const timer = setTimeout(() => setParamLater('q', qInput === '' ? null : qInput), 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qInput]);
  // setParam 的延后引用(effect 先于 setParam 定义,用 ref 规避依赖)
  const setParamRef = useRef<(key: string, value: string | null) => void>(() => undefined);
  function setParamLater(key: string, value: string | null): void {
    setParamRef.current(key, value);
  }
  // M12:`?create=1` 展开快速创建(快捷键 `c` 入口)
  const createParam = searchParams.get('create');

  const [workspace, setWorkspace] = useState<Membership | null>(null);
  const [currentMemberId, setCurrentMemberId] = useState<string | null>(null);
  const [roster, setRoster] = useState<MemberSummary[]>([]);
  const [issues, setIssues] = useState<IssueSummary[]>([]);
  const [statuses, setStatuses] = useState<IssueStatusRef[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(createParam === '1');
  const [hasLoaded, setHasLoaded] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    if (createParam === '1') setCreateOpen(true);
  }, [createParam]);

  // 本人 member id 变化不触发重新拉取(仅 mineOnly 时需要);经 ref 读取,
  // 避免无谓的重复请求(mineOnly 切换本身在依赖中,会重拉)。
  const currentMemberIdRef = useRef<string | null>(null);
  currentMemberIdRef.current = currentMemberId;
  // t 的函数身份每次渲染都变;经 ref 读取,避免 load 反复重建引发重叠请求竞态。
  const tRef = useRef(t);
  tRef.current = t;
  // 请求序号闸:乱序到达的旧响应不得覆盖新结果(过滤切换竞态防护)
  const loadSeqRef = useRef(0);

  // 解析工作区 + 本人 member id(经名册邮箱匹配;assignee=我过滤需要,§4.1 我的任务视角)
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const me = await fetchMe(client);
      const ws = activeWorkspace(me.memberships);
      if (cancelled) return;
      setWorkspace(ws);
      if (ws !== null) {
        const roster = await listMembers(client, ws.workspace_id, { limit: 100 });
        setRoster(roster.data);
        if (cancelled) return;
        const mine = roster.data.find(
          (m) =>
            m.member_type === 'human' &&
            m.profile !== null &&
            'email' in m.profile &&
            m.profile.email === me.user.email,
        );
        setCurrentMemberId(mine?.id ?? null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client]);

  const load = useCallback(async (): Promise<void> => {
    if (workspace === null) {
      setIsLoading(false);
      return;
    }
    // B4:mine 过滤需等本人 member id 解析完成,否则首载显示全量
    if (mineOnly && currentMemberId === null) return;
    setIsLoading(true);
    setError(null);
    const seq = ++loadSeqRef.current;
    try {
      const [page, defs] = await Promise.all([
        listIssues(client, workspace.workspace_id, {
          q: qFilter === '' ? undefined : qFilter,
          state_category:
            categoryFilter === ALL ? undefined : (categoryFilter as StateCategory),
          priority: priorityFilter === ALL ? undefined : (priorityFilter as IssuePriority),
          assignee_id:
            mineOnly && currentMemberIdRef.current !== null
              ? currentMemberIdRef.current
              : undefined,
          sort: 'created_at',
          order: 'desc',
          limit: PAGE_LIMIT,
        }),
        listStatuses(client, workspace.workspace_id),
      ]);
      if (seq !== loadSeqRef.current) return; // 旧响应:丢弃,不覆盖新结果
      setIssues([...page.data]);
      setNextCursor(page.nextCursor);
      setStatuses([...defs]);
    } catch (err: unknown) {
      if (seq !== loadSeqRef.current) return;
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      setError(tRef.current(key));
    } finally {
      if (seq === loadSeqRef.current) {
        setIsLoading(false);
        setHasLoaded(true);
      }
    }
    // currentMemberId 仅在 mine 过滤时影响结果,避免无关的重拉(首载竞态 B4)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, workspace, qFilter, categoryFilter, priorityFilter, mineOnly, mineOnly ? currentMemberId : null]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  // 实时增量合并(§3.6:按 id 合并,不整页刷新;§6.7 可见性水位)
  useEffect(() => {
    if (workspace === null || realtime === null) return;
    const channel = workspaceIssuesChannel(workspace.workspace_id);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      setIssues((prev) =>
        applyIssueListFrame(prev, frame, (issue) =>
          matchesFilters(issue, categoryFilter, priorityFilter, mineOnly, currentMemberId, qFilter),
        ),
      );
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, workspace, categoryFilter, priorityFilter, mineOnly, currentMemberId, qFilter]);

  const loadMore = useCallback(async (): Promise<void> => {
    if (workspace === null || nextCursor === null) return;
    const seq = ++loadSeqRef.current;
    try {
    const page = await listIssues(client, workspace.workspace_id, {
      q: qFilter === '' ? undefined : qFilter,
      state_category: categoryFilter === ALL ? undefined : (categoryFilter as StateCategory),
      priority: priorityFilter === ALL ? undefined : (priorityFilter as IssuePriority),
      assignee_id:
        mineOnly && currentMemberIdRef.current !== null
          ? currentMemberIdRef.current
          : undefined,
      sort: 'created_at',
      order: 'desc',
      limit: PAGE_LIMIT,
      cursor: nextCursor,
    });
    if (seq !== loadSeqRef.current) return;
    if (seq !== loadSeqRef.current) return;
    setIssues((prev) => {
      const seen = new Set(prev.map((issue) => issue.id));
      return [...prev, ...page.data.filter((issue) => !seen.has(issue.id))];
    });
    setNextCursor(page.nextCursor);
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      toast.addToast(tRef.current(key), { tone: 'danger', closeLabel: tRef.current('common.close') });
    }
  }, [client, workspace, nextCursor, qFilter, categoryFilter, priorityFilter, mineOnly]);

  const setParam = useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(searchParams);
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );
  setParamRef.current = setParam;

  const toggleSelect = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  if (workspace === null && !isLoading && error === null) {
    return <EmptyState title={t('state.emptyTitle')} description={t('issues.noWorkspace')} />;
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
  if (isLoading && !hasLoaded) {
    return <Skeleton loadingLabel={t('common.loading')} />;
  }

  return (
    <div className="mesh-issues">
      <header className="mesh-issues__head">
        <h1>{t('issues.pageTitle')}</h1>
        <Button onClick={() => setCreateOpen((v) => !v)} data-testid="issue-open-create">
          {t('issues.create.open')}
        </Button>
      </header>

      {createOpen ? (
        <QuickCreateForm
          workspace={workspace}
          members={roster}
          onCreated={(created) => {
            // F3:新建结果遵循当前过滤水位;不匹配则重拉(而非错误前置)
            if (
              matchesFilters(
                created,
                categoryFilter,
                priorityFilter,
                mineOnly,
                currentMemberId,
                qFilter,
              )
            ) {
              setIssues((prev) => [created, ...prev.filter((i) => i.id !== created.id)]);
            } else {
              setReloadKey((k) => k + 1);
            }
          }}
          onClose={() => {
            setCreateOpen(false);
            if (createParam === '1') setParam('create', null);
          }}
        />
      ) : null}

      <div className="mesh-issues__filters" role="search">
        <input
          type="search"
          value={qInput}
          onChange={(event) => setQInput(event.target.value)}
          placeholder={t('issues.filters.search')}
          aria-label={t('issues.filters.search')}
          data-testid="issue-filter-q"
        />
        <Select
          label={t('issues.filters.category')}
          value={categoryFilter}
          onChange={(event) =>
            setParam('category', event.target.value === ALL ? null : event.target.value)
          }
        >
          <option value={ALL}>{t('issues.filters.all')}</option>
          {STATE_CATEGORY_ORDER.map((c) => (
            <option key={c} value={c}>
              {t(`issues.category.${c}`)}
            </option>
          ))}
        </Select>
        <Select
          label={t('issues.priority.label')}
          value={priorityFilter}
          onChange={(event) =>
            setParam('priority', event.target.value === ALL ? null : event.target.value)
          }
        >
          <option value={ALL}>{t('issues.filters.all')}</option>
          {PRIORITY_ORDER.map((p) => (
            <option key={p} value={p}>
              {t(`issues.priority.${p}`)}
            </option>
          ))}
        </Select>
        <label className="mesh-issues__mine">
          <input
            type="checkbox"
            checked={mineOnly}
            onChange={(event) => setParam('mine', event.target.checked ? 'true' : null)}
            data-testid="issue-filter-mine"
          />
          {t('issues.filters.mine')}
        </label>
      </div>

      {issues.length === 0 ? (
        <EmptyState
          title={t('issues.empty.title')}
          description={t('issues.empty.description')}
          action={<Button onClick={() => setCreateOpen(true)}>{t('issues.create.open')}</Button>}
        />
      ) : (
        <table className="mesh-issues__table" data-testid="issue-table">
          <thead>
            <tr>
              <th aria-label={t('issues.columns.select')} />
              <th>{t('issues.columns.key')}</th>
              <th>{t('issues.columns.title')}</th>
              <th>{t('issues.columns.status')}</th>
              <th>{t('issues.columns.priority')}</th>
              <th>{t('issues.columns.assignee')}</th>
              <th>{t('issues.columns.due')}</th>
            </tr>
          </thead>
          <tbody>
            {issues.map((issue) => (
              <tr key={issue.id} data-testid={`issue-row-${issue.identifier}`}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(issue.id)}
                    onChange={() => toggleSelect(issue.id)}
                    aria-label={t('issues.columns.select')}
                    data-testid={`issue-select-${issue.id}`}
                  />
                </td>
                <td className="mesh-issues__identifier">{issue.identifier}</td>
                <td>
                  <Link to={`/issues/${issue.id}`} className="mesh-issues__title-link">
                    {issue.title}
                  </Link>
                </td>
                <td>
                  <span
                    className="mesh-issues__status-chip"
                    data-testid={`issue-status-${issue.id}`}
                    style={
                      issue.status != null && issue.status.color != null
                        ? { borderColor: issue.status.color }
                        : undefined
                    }
                  >
                    {issue.status != null ? issue.status.name : t(`issues.category.${issue.state_category}`)}
                  </span>
                </td>
                <td>{t(`issues.priority.${issue.priority}`)}</td>
                <td>
                  {issue.assignee != null
                    ? `${issue.assignee.name}${issue.assignee.member_type === 'agent' ? ` (${t('issues.agentBadge')})` : ''}`
                    : t('issues.unassigned')}
                </td>
                <td>{formatDueDate(issue.due_date, intl.locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {nextCursor !== null ? (
        <div className="mesh-issues__more">
          <Button variant="secondary" onClick={() => void loadMore()}>
            {t('issues.loadMore')}
          </Button>
        </div>
      ) : null}

      {selected.size > 0 ? (
        <BulkBar
          selected={[...selected]}
          statuses={statuses}
          onDone={() => {
            setSelected(new Set());
            setReloadKey((k) => k + 1);
          }}
          onClear={() => setSelected(new Set())}
        />
      ) : null}
    </div>
  );
}
