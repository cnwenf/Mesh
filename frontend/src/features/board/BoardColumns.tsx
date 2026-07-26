/**
 * 看板列容器 + 卡片(kanban.md §4.2/§4.3)。
 *
 * 投影层:列头(状态色 + 名称 + 计数 + WIP 徽章,warn 黄 / block 红)+ 真实卡片
 * (可拖拽)+ 列体落点(HTML5 DnD,浮点中点法定位)+ 列底快速创建(继承分组值)。
 * WIP block 已满列禁用落点(拖拽过程不高亮,§4.4);卡片字段受 card_fields 约束
 * 的完整呈现随显示字段增量,此处呈现 identifier/标题/优先级/负责人核心字段。
 */
/* eslint-disable react-refresh/only-export-components -- categoryColorClass/computeDropPosition 与列组件同模块契约 */
import { useState } from 'react';
import { useT } from '../../i18n';
import type { BoardCard } from './projection';
import type { BoardColumn } from './types';

const CARD_MIME = 'text/mesh-card-id';

/** 状态类别的语义色 token(经 CSS 变量引用,禁硬编码色值,§6.12)。 */
export function categoryColorClass(key: string): string {
  return `mesh-board__dot--${key}`;
}

/**
 * 浮点中点法定位(kanban §4.3):插入 index 处取相邻中点;列顶 = 首张 -1;
 * 列底/空列 = 末张 +1(空列 = 1)。
 */
export function computeDropPosition(
  cards: readonly BoardCard[],
  index: number | null,
): number {
  if (cards.length === 0) return 1;
  if (index === null || index >= cards.length) {
    return (cards[cards.length - 1]?.position ?? 0) + 1;
  }
  if (index <= 0) {
    return (cards[0]?.position ?? 0) - 1;
  }
  const before = cards[index - 1]?.position ?? 0;
  const after = cards[index]?.position ?? 0;
  return (before + after) / 2;
}

interface BoardCardProps {
  readonly card: BoardCard;
  readonly index: number;
  readonly draggable: boolean;
  readonly onDropOnCard: (issueId: string, index: number) => void;
}

function BoardCardItem({ card, index, draggable, onDropOnCard }: BoardCardProps): React.JSX.Element {
  return (
    <div
      className="mesh-board__card"
      data-testid={`board-card-${card.id}`}
      draggable={draggable}
      onDragStart={(event) => {
        event.dataTransfer.setData(CARD_MIME, card.id);
        event.dataTransfer.effectAllowed = 'move';
      }}
      onDragOver={(event) => {
        if (draggable) event.preventDefault();
      }}
      onDrop={(event) => {
        event.preventDefault();
        event.stopPropagation();
        const id = event.dataTransfer.getData(CARD_MIME);
        if (id !== '') onDropOnCard(id, index);
      }}
    >
      <span className="mesh-board__card-id">{card.identifier}</span>
      <span className="mesh-board__card-title">{card.title}</span>
      <span className={`mesh-board__card-priority mesh-board__card-priority--${card.priority}`}>
        {card.priority}
      </span>
      {card.assignee !== null ? (
        <span className="mesh-board__card-assignee" title={card.assignee.name}>
          {card.assignee.name}
        </span>
      ) : null}
    </div>
  );
}

interface BoardColumnCardProps {
  readonly column: BoardColumn;
  readonly groupBy: string | null;
  readonly cards: readonly BoardCard[];
  readonly canWrite: boolean;
  readonly dragEnabled: boolean;
  readonly onToggleCollapse: (key: string) => void;
  readonly onDropCard: (issueId: string, toGroupKey: string, position: number) => void;
  readonly onQuickCreate: (groupKey: string, title: string) => void;
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

function QuickCreate({
  groupKey,
  canWrite,
  onQuickCreate,
}: {
  groupKey: string;
  canWrite: boolean;
  onQuickCreate: (groupKey: string, title: string) => void;
}): React.JSX.Element {
  const t = useT();
  const [title, setTitle] = useState('');
  const submit = (): void => {
    const trimmed = title.trim();
    if (trimmed === '') return;
    onQuickCreate(groupKey, trimmed);
    setTitle('');
  };
  return (
    <div className="mesh-board__quick-create">
      <input
        className="mesh-board__quick-create-input"
        placeholder={t('board.quickAdd')}
        value={title}
        disabled={!canWrite}
        data-testid={`quick-add-${groupKey}`}
        onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') submit();
        }}
      />
    </div>
  );
}

function BoardColumnCard(props: BoardColumnCardProps): React.JSX.Element {
  const { column, groupBy, cards, canWrite, dragEnabled, onToggleCollapse, onDropCard, onQuickCreate } =
    props;
  const t = useT();
  const isDynamic = groupBy !== null && groupBy !== 'state_category' && groupBy !== 'priority';
  const label =
    column.key === '__dynamic__'
      ? t('board.dynamicColumnsPlaceholder', { groupBy: groupBy ?? '' })
      : isDynamic
        ? column.label
        : t(column.label);
  // WIP block 满载视觉提示(§4.4)。真正的硬阻止由服务端在 /moves 事务内强制
  // (422 wip_limit_exceeded → 弹回 + toast),客户端不预先禁用落点,否则用户得不到
  // 拒收反馈。
  const dropBlocked =
    column.wip !== null && column.wip.enforcement === 'block' && column.count >= column.wip.limit;

  return (
    <section
      className="mesh-board__column"
      data-testid={`board-column-${column.key}`}
      aria-label={label}
    >
      <header className="mesh-board__column-head">
        <span className={`mesh-board__dot ${categoryColorClass(column.key)}`} aria-hidden="true" />
        <span className="mesh-board__column-name">{label}</span>
        <span className="mesh-board__count" data-testid={`count-${column.key}`}>
          {column.count}
        </span>
        <WipBadge column={column} />
        <button
          type="button"
          className="mesh-board__collapse"
          aria-expanded={!column.collapsed}
          aria-label={t(column.collapsed ? 'board.expandColumn' : 'board.collapseColumn', {
            name: label,
          })}
          onClick={() => onToggleCollapse(column.key)}
        >
          {column.collapsed ? '▸' : '▾'}
        </button>
      </header>
      {column.collapsed ? null : (
        <div
          className={`mesh-board__column-body ${dropBlocked ? 'mesh-board__column-body--blocked' : ''}`.trim()}
          data-testid={`column-body-${column.key}`}
          onDragOver={(event) => {
            if (dragEnabled) event.preventDefault();
          }}
          onDrop={(event) => {
            event.preventDefault();
            const id = event.dataTransfer.getData(CARD_MIME);
            if (id === '' || !dragEnabled) return;
            onDropCard(id, column.key, computeDropPosition(cards, null));
          }}
        >
          {cards.length === 0 ? (
            <p className="mesh-board__column-empty">{t('board.columnEmptyTitle')}</p>
          ) : (
            cards.map((card, index) => (
              <BoardCardItem
                key={card.id}
                card={card}
                index={index}
                draggable={dragEnabled}
                onDropOnCard={(issueId, cardIndex) =>
                  onDropCard(issueId, column.key, computeDropPosition(cards, cardIndex))
                }
              />
            ))
          )}
          <QuickCreate groupKey={column.key} canWrite={canWrite} onQuickCreate={onQuickCreate} />
        </div>
      )}
    </section>
  );
}

interface BoardColumnsProps {
  readonly columns: readonly BoardColumn[];
  readonly groupBy: string | null;
  readonly cardsByKey: Readonly<Record<string, readonly BoardCard[]>>;
  readonly canWrite: boolean;
  readonly dragEnabled: boolean;
  readonly onToggleCollapse: (key: string) => void;
  readonly onDropCard: (issueId: string, toGroupKey: string, position: number) => void;
  readonly onQuickCreate: (groupKey: string, title: string) => void;
}

export function BoardColumns(props: BoardColumnsProps): React.JSX.Element {
  const {
    columns,
    groupBy,
    cardsByKey,
    canWrite,
    dragEnabled,
    onToggleCollapse,
    onDropCard,
    onQuickCreate,
  } = props;
  return (
    <div className="mesh-board__columns" role="list" data-testid="board-columns">
      {columns.map((column) => (
        <div role="listitem" key={column.key}>
          <BoardColumnCard
            column={column}
            groupBy={groupBy}
            cards={cardsByKey[column.key] ?? []}
            canWrite={canWrite}
            dragEnabled={dragEnabled}
            onToggleCollapse={onToggleCollapse}
            onDropCard={onDropCard}
            onQuickCreate={onQuickCreate}
          />
        </div>
      ))}
    </div>
  );
}
