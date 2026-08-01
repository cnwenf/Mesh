/**
 * 小队任务详情页(squad.md §4.4):
 * 状态 + 进度条;待审批横幅(awaiting_plan_approval:方案 Markdown + 批准/驳回,§6.10);
 * 拆解树视图(缩进子任务 / 状态点 / 负责人 / 阶段 / blocked_by「等待 X」);取消按钮;
 * 非终态时每 3s 轮询 getTaskStatus,状态变化即重拉整树(§3.1 轻量状态查询)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { Banner, Button, ErrorState, Icon, Skeleton, StatusDot, useToast } from '../../design';
import { useUgcColorGuard } from '../../design/ugcColorGuard';
import { env } from '../../env';
import { useT } from '../../i18n';
import { useRealtimeContext } from '../../shell/AppShell';
import { activeWorkspace, fetchMe } from '../members/api';
import type { Membership } from '../members/types';
import { renderMarkdownPreview } from '../comments/markdown';
import {
  approvePlan,
  cancelTask,
  getTaskStatus,
  getTaskTree,
  moveTaskStatus,
  rejectPlan,
  squadChannel,
  taskStreamUrl,
} from './api';
import { SquadTaskKanban } from './SquadTaskKanban';
import { useTaskStream } from './useTaskStream';
import type { SquadTask, SquadTaskStatus } from './types';
import { TASK_STATUS_TONE, TERMINAL_TASK_STATUSES } from './types';
import './squads.css';

const TASK_POLL_INTERVAL_MS = 3000;
const PROGRESS_FULL = 100;

/** 深度优先收集整树任务(含根),供 blocked_by 标题解析。 */
function flattenTree(task: SquadTask): ReadonlyMap<string, SquadTask> {
  const map = new Map<string, SquadTask>();
  const visit = (node: SquadTask): void => {
    map.set(node.id, node);
    for (const child of node.children ?? []) visit(child);
  };
  visit(task);
  return map;
}

interface TreeNodeProps {
  readonly task: SquadTask;
  readonly depth: number;
  readonly index: ReadonlyMap<string, SquadTask>;
}

function TaskTreeNode(props: TreeNodeProps): React.JSX.Element {
  const t = useT();
  const { task, depth, index } = props;
  const blockers = task.blocked_by.map((id) => index.get(id)?.title_snapshot ?? id).join(', ');
  const dependencies = task.depends_on.map((id) => index.get(id)?.title_snapshot ?? id).join(', ');
  return (
    <>
      <li
        className="mesh-squads__tree-node"
        style={{ paddingInlineStart: `calc(var(--space-3) * ${depth})` }}
        data-testid={`squad-tree-node-${task.id}`}
      >
        <StatusDot
          tone={TASK_STATUS_TONE[task.status]}
          label={t(`squads.task.status.${task.status}`)}
        />
        <span className="mesh-squads__tree-title">{task.title_snapshot ?? task.id}</span>
        <span
          className="mesh-squads__tree-assignee mesh-squads__identity"
          data-testid={`squad-tree-assignee-${task.id}`}
        >
          {task.assignee !== null ? (
            <>
              <Icon name={task.assignee.member_type === 'agent' ? 'agent' : 'user'} size={16} />
              <span>{task.assignee.name}</span>
            </>
          ) : (
            t('squads.task.unassigned')
          )}
        </span>
        {task.stage !== null ? (
          <span className="mesh-squads__tree-stage">
            {t('squads.task.stage')} {task.stage}
          </span>
        ) : null}
        {task.blocked_by.length > 0 ? (
          <span className="mesh-squads__tree-blocked" data-testid={`squad-tree-blocked-${task.id}`}>
            {t('squads.task.waitingOn', { count: task.blocked_by.length, names: blockers })}
          </span>
        ) : null}
        {task.depends_on.length > 0 ? (
          <span
            className="mesh-squads__tree-dependencies"
            data-testid={`squad-tree-dependencies-${task.id}`}
          >
            <Icon name="link" size={16} />
            <span>{dependencies}</span>
          </span>
        ) : null}
        {task.failure_reason !== null && task.failure_reason !== '' ? (
          <span className="mesh-squads__tree-blocked">{task.failure_reason}</span>
        ) : null}
      </li>
      {(task.children ?? []).map((child) => (
        <TaskTreeNode key={child.id} task={child} depth={depth + 1} index={index} />
      ))}
    </>
  );
}

type TaskViewMode = 'tree' | 'kanban';

export function SquadTaskDetailPage(): React.JSX.Element {
  const t = useT();
  const ugcGuard = useUgcColorGuard();
  const toast = useToast();
  const { squadId, taskId } = useParams<{ squadId: string; taskId: string }>();
  const realtime = useRealtimeContext();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);

  const [workspace, setWorkspace] = useState<Membership | null>(null);
  const [task, setTask] = useState<SquadTask | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [view, setView] = useState<TaskViewMode>('tree');

  const tRef = useRef(t);
  tRef.current = t;
  const taskRef = useRef<SquadTask | null>(null);
  taskRef.current = task;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const me = await fetchMe(client);
      const active = activeWorkspace(me.memberships);
      if (cancelled) return;
      setWorkspace(active);
    })();
    return () => {
      cancelled = true;
    };
  }, [client]);

  const load = useCallback(async (): Promise<void> => {
    if (workspace === null || squadId === undefined || taskId === undefined) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const tree = await getTaskTree(client, workspace.workspace_id, squadId, taskId);
      setTask(tree);
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      setError(tRef.current(key));
    } finally {
      setIsLoading(false);
    }
  }, [client, workspace, squadId, taskId]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  // 轮询:非终态时每 3s 查状态,变化即重拉整树。
  const pollOnce = useCallback(async (): Promise<void> => {
    if (workspace === null || squadId === undefined || taskId === undefined) return;
    const current = taskRef.current;
    if (current === null || TERMINAL_TASK_STATUSES.has(current.status)) return;
    try {
      const view = await getTaskStatus(client, workspace.workspace_id, squadId, taskId);
      if (view.status !== current.status) setReloadKey((k) => k + 1);
    } catch {
      // 轮询瞬时失败静默跳过,下个 tick 再试(不打断页面)。
    }
  }, [client, workspace, squadId, taskId]);

  const isTerminal = task !== null && TERMINAL_TASK_STATUSES.has(task.status);

  useEffect(() => {
    if (isTerminal) return;
    const id = setInterval(() => {
      void pollOnce();
    }, TASK_POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [isTerminal, pollOnce]);

  // 实时:订阅 squad:{id} 频道,任意帧触发整树重取(§3.5;agent 驱动任务亦经此流入)。
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

  // SSE 编排流(§3.2 / §6.8):非终态时以 fetch 流式消费,命中五类事件即重取;
  // 流不可用时静默退出,由上方 3s 轮询兜底(§3.5 降级)。
  const streamUrl = useMemo(
    () =>
      workspace !== null && squadId !== undefined && taskId !== undefined
        ? taskStreamUrl(workspace.workspace_id, squadId, taskId)
        : null,
    [workspace, squadId, taskId],
  );
  useTaskStream({
    url: streamUrl,
    enabled: !isTerminal,
    onEvent: () => setReloadKey((k) => k + 1),
  });

  // 看板人工改状(§4.2):服务端校验迁移,非法 → 409 conflict(toast + 重取)。
  const onMoveTask = useCallback(
    async (movedTaskId: string, status: SquadTaskStatus): Promise<void> => {
      if (workspace === null || squadId === undefined) return;
      try {
        await moveTaskStatus(client, workspace.workspace_id, squadId, movedTaskId, { status });
        toast.addToast(t('squads.kanban.moved'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
        setReloadKey((k) => k + 1);
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
        setReloadKey((k) => k + 1);
      }
    },
    [client, workspace, squadId, toast, t],
  );

  const decide = useCallback(
    async (approve: boolean) => {
      if (workspace === null || squadId === undefined || taskId === undefined) return;
      try {
        if (approve) {
          await approvePlan(client, workspace.workspace_id, squadId, taskId);
        } else {
          await rejectPlan(client, workspace.workspace_id, squadId, taskId);
        }
        toast.addToast(t(approve ? 'squads.toast.approved' : 'squads.toast.rejected'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
        setReloadKey((k) => k + 1);
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [client, workspace, squadId, taskId, toast, t],
  );

  const onCancel = useCallback(async (): Promise<void> => {
    if (workspace === null || squadId === undefined || taskId === undefined) return;
    try {
      await cancelTask(client, workspace.workspace_id, squadId, taskId);
      toast.addToast(t('squads.toast.cancelled'), {
        tone: 'success',
        closeLabel: t('common.close'),
      });
      setReloadKey((k) => k + 1);
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    }
  }, [client, workspace, squadId, taskId, toast, t]);

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
  if (isLoading || task === null) {
    return <Skeleton loadingLabel={t('common.loading')} />;
  }

  const progress = task.progress;
  const doneCount = progress?.done ?? 0;
  const totalCount = progress?.total ?? 0;
  const progressPct =
    totalCount > 0
      ? Math.min(PROGRESS_FULL, Math.round((doneCount / totalCount) * PROGRESS_FULL))
      : 0;
  const index = flattenTree(task);
  /** 看板用子任务(不含根):整树节点剔除根本身。 */
  const subtasks = [...index.values()].filter((node) => node.id !== task.id);
  const planHtml =
    task.plan_markdown !== null && task.plan_markdown !== ''
      ? renderMarkdownPreview(task.plan_markdown)
      : '';

  return (
    <div className="mesh-squads" data-testid="squad-task-page">
      <header className="mesh-squads__head">
        <Link to={`/squads/${task.squad_id}`} className="mesh-squads__back">
          {t('squads.back')}
        </Link>
        <h1 data-testid="squad-task-title">{task.title_snapshot ?? task.id}</h1>
        <span data-testid="squad-task-status">
          <StatusDot
            tone={TASK_STATUS_TONE[task.status]}
            label={t(`squads.task.status.${task.status}`)}
          />
        </span>
        {!isTerminal ? (
          <Button
            variant="danger"
            size="sm"
            onClick={() => void onCancel()}
            data-testid="squad-task-cancel"
          >
            {t('squads.task.cancel')}
          </Button>
        ) : null}
      </header>

      <div
        className="mesh-squads__progress"
        role="meter"
        aria-valuenow={progressPct}
        aria-valuemin={0}
        aria-valuemax={PROGRESS_FULL}
        aria-label={t('squads.task.progress')}
        data-testid="squad-task-progress"
      >
        <div className="mesh-squads__progress-fill" style={{ width: `${progressPct}%` }} />
      </div>
      <p className="mesh-squads__progress-label" data-testid="squad-task-progress-label">
        {t('squads.task.progressLabel', { done: doneCount, total: totalCount })}
      </p>

      {task.status === 'awaiting_plan_approval' ? (
        <Banner tone="warn" politeness="assertive">
          <div
            className="mesh-squads__decision-card"
            data-testid="squad-task-approval"
            aria-labelledby="squad-task-approval-title"
          >
            <div className="mesh-squads__decision-copy">
              <div className="mesh-squads__decision-heading">
                <h2 id="squad-task-approval-title">{t('squads.task.approvalTitle')}</h2>
                <StatusDot tone="warn" label={t('squads.task.status.awaiting_plan_approval')} />
              </div>
              <p>{t('squads.task.approvalHint')}</p>
              <div className="mesh-squads__approval-actions">
                <Button onClick={() => void decide(true)} data-testid="squad-task-approve">
                  {t('squads.task.approve')}
                </Button>
                <Button
                  variant="danger"
                  onClick={() => void decide(false)}
                  data-testid="squad-task-reject"
                >
                  {t('squads.task.reject')}
                </Button>
              </div>
            </div>
            {planHtml !== '' ? (
              <aside className="mesh-squads__decision-plan" aria-label={t('squads.task.subtasks')}>
                <div
                  className="mesh-squads__plan"
                  data-testid="squad-task-plan"
                  ref={ugcGuard}
                  dangerouslySetInnerHTML={{ __html: planHtml }}
                />
              </aside>
            ) : null}
          </div>
        </Banner>
      ) : null}

      {task.result_summary !== null && task.result_summary !== '' ? (
        <p className="mesh-squads__result" data-testid="squad-task-result">
          {task.result_summary}
        </p>
      ) : null}

      <section className="mesh-squads__pane" data-testid="squad-task-tree-pane">
        <div className="mesh-squads__pane-head">
          <h2>{t('squads.task.subtasks')}</h2>
          <div
            className="mesh-squads__segmented"
            role="tablist"
            aria-label={t('squads.task.viewLabel')}
          >
            <button
              type="button"
              role="tab"
              aria-selected={view === 'tree'}
              className="mesh-squads__segmented-btn"
              data-testid="squad-view-tree"
              onClick={() => setView('tree')}
            >
              {t('squads.task.treeView')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === 'kanban'}
              className="mesh-squads__segmented-btn"
              data-testid="squad-view-kanban"
              onClick={() => setView('kanban')}
            >
              {t('squads.task.kanbanView')}
            </button>
          </div>
        </div>

        {view === 'tree' ? (
          (task.children ?? []).length === 0 ? (
            <p className="mesh-squads__pane-empty">{t('squads.task.noSubtasks')}</p>
          ) : (
            <ul className="mesh-squads__tree">
              {(task.children ?? []).map((child) => (
                <TaskTreeNode key={child.id} task={child} depth={0} index={index} />
              ))}
            </ul>
          )
        ) : subtasks.length === 0 ? (
          <p className="mesh-squads__pane-empty">{t('squads.task.noSubtasks')}</p>
        ) : (
          <SquadTaskKanban tasks={subtasks} index={index} onMoveTask={onMoveTask} />
        )}
      </section>
    </div>
  );
}
