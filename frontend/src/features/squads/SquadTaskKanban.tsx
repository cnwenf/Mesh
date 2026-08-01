/**
 * 小队任务看板视图(squad.md §4.1 / §4.2):子任务按状态分列,原生 HTML5 拖拽改状。
 * 列:Pending(pending+dispatching)/ In progress / Blocked / Done / Failed(failed+cancelled)。
 * 拖放映射:In progress→in_progress、Done→done、Failed→failed、Blocked→blocked;
 * Pending 列仅接受 blocked 来源(解阻 → in_progress)。映射后交父级调 moveTaskStatus,
 * 非法迁移由服务端 409 拒绝(父级 toast + 重取);客户端判定的无效落点就地提示。
 */
import { useCallback, useState } from 'react';
import { Icon, StatusDot, useToast } from '../../design';
import { useT } from '../../i18n';
import type { SquadTask, SquadTaskStatus } from './types';

const TASK_MIME = 'text/mesh-squad-task-id';

type KanbanColumnKey = 'pending' | 'in_progress' | 'blocked' | 'done' | 'failed';

type ColumnTone = 'success' | 'warn' | 'danger' | 'info' | 'neutral';

interface KanbanColumnDef {
  readonly key: KanbanColumnKey;
  readonly statuses: readonly SquadTaskStatus[];
  readonly tone: ColumnTone;
}

const KANBAN_COLUMNS: readonly KanbanColumnDef[] = [
  { key: 'pending', statuses: ['pending', 'dispatching'], tone: 'neutral' },
  { key: 'in_progress', statuses: ['in_progress'], tone: 'info' },
  { key: 'blocked', statuses: ['blocked'], tone: 'warn' },
  { key: 'done', statuses: ['done'], tone: 'success' },
  { key: 'failed', statuses: ['failed', 'cancelled'], tone: 'danger' },
];

/** 未显式归列的中间态(拆解 / 待审批 / 聚合)回退 Pending 列,确保无卡片丢失。 */
const COLUMN_BY_STATUS: ReadonlyMap<SquadTaskStatus, KanbanColumnKey> = new Map(
  KANBAN_COLUMNS.flatMap((column) =>
    column.statuses.map((status) => [status, column.key] as const),
  ),
);

function columnOf(status: SquadTaskStatus): KanbanColumnKey {
  return COLUMN_BY_STATUS.get(status) ?? 'pending';
}

/** 落点列 + 源状态 → 目标状态;null = 客户端判定的无效迁移。 */
function targetStatusFor(columnKey: KanbanColumnKey, card: SquadTask): SquadTaskStatus | null {
  switch (columnKey) {
    case 'pending':
      return card.status === 'blocked' ? 'in_progress' : null;
    case 'in_progress':
      return 'in_progress';
    case 'blocked':
      return 'blocked';
    case 'done':
      return 'done';
    case 'failed':
      return 'failed';
  }
}

interface KanbanCardProps {
  readonly task: SquadTask;
  readonly index: ReadonlyMap<string, SquadTask>;
}

function KanbanCard(props: KanbanCardProps): React.JSX.Element {
  const t = useT();
  const { task, index } = props;
  const blockers = task.blocked_by.map((id) => index.get(id)?.title_snapshot ?? id).join(', ');
  const dependencies = task.depends_on.map((id) => index.get(id)?.title_snapshot ?? id).join(', ');
  return (
    <li
      className="mesh-squads__kanban-card"
      draggable
      data-testid={`squad-kanban-card-${task.id}`}
      onDragStart={(event) => {
        event.dataTransfer.setData(TASK_MIME, task.id);
        event.dataTransfer.effectAllowed = 'move';
      }}
    >
      <span className="mesh-squads__kanban-card-title">{task.title_snapshot ?? task.id}</span>
      <span className="mesh-squads__kanban-card-assignee mesh-squads__identity">
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
        <span className="mesh-squads__kanban-card-stage">
          {t('squads.task.stage')} {task.stage}
        </span>
      ) : null}
      {task.blocked_by.length > 0 ? (
        <span
          className="mesh-squads__kanban-card-blocked"
          data-testid={`squad-kanban-blocked-${task.id}`}
        >
          {t('squads.task.waitingOn', { count: task.blocked_by.length, names: blockers })}
        </span>
      ) : null}
      {task.depends_on.length > 0 ? (
        <span className="mesh-squads__kanban-card-dependencies">
          <Icon name="link" size={16} />
          <span>{dependencies}</span>
        </span>
      ) : null}
      {task.failure_reason !== null && task.failure_reason !== '' ? (
        <span className="mesh-squads__kanban-card-blocked">{task.failure_reason}</span>
      ) : null}
    </li>
  );
}

export interface SquadTaskKanbanProps {
  /** 扁平化后的子任务(不含根)。 */
  readonly tasks: readonly SquadTask[];
  /** 整树索引:供 blocked_by 标题解析。 */
  readonly index: ReadonlyMap<string, SquadTask>;
  /** 合法映射落点 → 父级调 moveTaskStatus(含 409 toast + 重取)。 */
  readonly onMoveTask: (taskId: string, status: SquadTaskStatus) => Promise<void>;
}

export function SquadTaskKanban(props: SquadTaskKanbanProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const { tasks, index, onMoveTask } = props;
  const [dragOverColumn, setDragOverColumn] = useState<KanbanColumnKey | null>(null);

  const handleDrop = useCallback(
    (event: React.DragEvent, columnKey: KanbanColumnKey) => {
      event.preventDefault();
      setDragOverColumn(null);
      const taskId = event.dataTransfer.getData(TASK_MIME);
      if (taskId === '') return;
      const card = index.get(taskId);
      if (card === undefined) return;
      const target = targetStatusFor(columnKey, card);
      if (target === null) {
        toast.addToast(t('squads.kanban.invalidMove'), {
          tone: 'warn',
          closeLabel: t('common.close'),
        });
        return;
      }
      if (target === card.status) return;
      void onMoveTask(taskId, target);
    },
    [index, onMoveTask, toast, t],
  );

  return (
    <div className="mesh-squads__kanban" data-testid="squad-kanban">
      {KANBAN_COLUMNS.map((column) => {
        const cards = tasks.filter((task) => columnOf(task.status) === column.key);
        return (
          <section
            key={column.key}
            className={
              dragOverColumn === column.key
                ? 'mesh-squads__kanban-col mesh-squads__kanban-col--over'
                : 'mesh-squads__kanban-col'
            }
            data-testid={`squad-kanban-col-${column.key}`}
            aria-label={t(`squads.kanban.column.${column.key}`)}
            onDragOver={(event) => {
              event.preventDefault();
              setDragOverColumn(column.key);
            }}
            onDragLeave={() =>
              setDragOverColumn((current) => (current === column.key ? null : current))
            }
            onDrop={(event) => handleDrop(event, column.key)}
          >
            <h3 className="mesh-squads__kanban-col-head">
              <StatusDot tone={column.tone} label={t(`squads.kanban.column.${column.key}`)} />
              <span
                className="mesh-squads__kanban-col-count"
                data-testid={`squad-kanban-count-${column.key}`}
              >
                {cards.length}
              </span>
            </h3>
            <ul className="mesh-squads__kanban-cards">
              {cards.map((task) => (
                <KanbanCard key={task.id} task={task} index={index} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
