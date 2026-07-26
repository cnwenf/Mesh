/**
 * 看板列容器(kanban.md §4.2):列头(状态色 + 名称 + 计数 + WIP 徽章)+
 * 折叠 + 空态列体(§6.12 empty)+ 列底快速创建占位(投影增量落地后接通)。
 * 定义层切片不接真实 issue 数据:计数恒 0,列体呈现空态。
 */
/* eslint-disable react-refresh/only-export-components -- categoryColorClass 与列组件同模块契约 */
import { EmptyState } from '../../design';
import { useT } from '../../i18n';
import type { BoardColumn } from './types';

/** 状态类别的语义色 token(经 CSS 变量引用,禁硬编码色值,§6.12)。 */
export function categoryColorClass(key: string): string {
  return `mesh-board__dot--${key}`;
}

interface BoardColumnCardProps {
  readonly column: BoardColumn;
  readonly groupBy: string | null;
  readonly onToggleCollapse: (key: string) => void;
}

function WipBadge({ column }: { column: BoardColumn }): React.JSX.Element | null {
  const t = useT();
  if (column.wip === null) return null;
  const exceeded = column.count > column.wip.limit;
  const toneClass =
    exceeded && column.wip.enforcement === 'block'
      ? 'mesh-board__wip--block'
      : exceeded
        ? 'mesh-board__wip--warn'
        : '';
  return (
    <span
      className={`mesh-board__wip ${toneClass}`.trim()}
      data-testid={`wip-badge-${column.key}`}
      title={t('board.wipBadgeTitle', {
        count: column.count,
        limit: column.wip.limit,
        enforcement: column.wip.enforcement,
      })}
    >
      {column.count}/{column.wip.limit}
    </span>
  );
}

function BoardColumnCard(props: BoardColumnCardProps): React.JSX.Element {
  const { column, groupBy, onToggleCollapse } = props;
  const t = useT();
  const isDynamic = groupBy !== null && groupBy !== 'state_category' && groupBy !== 'priority';
  const label = column.placeholder && column.key === '__dynamic__'
    ? t('board.dynamicColumnsPlaceholder', { groupBy: groupBy ?? '' })
    : column.key === '__dynamic__' || isDynamic
      ? column.key
      : t(column.label);
  return (
    <section
      className="mesh-board__column"
      data-testid={`board-column-${column.key}`}
      aria-label={label}
    >
      <header className="mesh-board__column-head">
        <span
          className={`mesh-board__dot ${categoryColorClass(column.key)}`}
          aria-hidden="true"
        />
        <span className="mesh-board__column-name">{label}</span>
        <span className="mesh-board__count" data-testid={`count-${column.key}`}>
          {column.count}
        </span>
        <WipBadge column={column} />
        <button
          type="button"
          className="mesh-board__collapse"
          aria-expanded={!column.collapsed}
          aria-label={t(
            column.collapsed ? 'board.expandColumn' : 'board.collapseColumn',
            { name: label },
          )}
          onClick={() => onToggleCollapse(column.key)}
        >
          {column.collapsed ? '▸' : '▾'}
        </button>
      </header>
      {column.collapsed ? null : (
        <div className="mesh-board__column-body">
          <EmptyState
            title={t('board.columnEmptyTitle')}
            description={t('board.columnEmptyDescription')}
          />
          <button
            type="button"
            className="mesh-board__quick-add"
            disabled
            title={t('board.quickAddDisabled')}
            data-testid={`quick-add-${column.key}`}
          >
            + {t('board.quickAdd')}
          </button>
        </div>
      )}
    </section>
  );
}

interface BoardColumnsProps {
  readonly columns: readonly BoardColumn[];
  readonly groupBy: string | null;
  readonly onToggleCollapse: (key: string) => void;
}

export function BoardColumns(props: BoardColumnsProps): React.JSX.Element {
  const { columns, groupBy, onToggleCollapse } = props;
  return (
    <div className="mesh-board__columns" role="list" data-testid="board-columns">
      {columns.map((column) => (
        <div role="listitem" key={column.key}>
          <BoardColumnCard
            column={column}
            groupBy={groupBy}
            onToggleCollapse={onToggleCollapse}
          />
        </div>
      ))}
    </div>
  );
}
