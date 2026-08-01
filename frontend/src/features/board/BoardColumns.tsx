/**
 * 看板列容器 + 卡片 + 交互编排(kanban.md §4.2/§4.3/§4.4,design-quality §8.1–8.3/§9.4/§10.2)。
 *
 * 投影层:列头(状态色 + 名称 + 计数 + WIP 徽章,warn 黄 / block 红,超限附 warning 图标)
 * + 真实卡片 + 列底快速创建(继承分组值)。
 *
 * 交互(design-quality §9.4 六规则):
 * - 指针拖拽(useBoardDrag):阈值进入、浮动副本(BoardDragLayer)、目标列高亮、
 *   落点指示线、WIP 预检条(warn 放行 / block 禁落)、Esc 取消、aria-live 播报;
 * - 键盘移动(useBoardKeyboardMove,§10.2 非拖拽替代路径):方向键选列/位,Enter 确认;
 * - 触摸长按(BoardTouchMoveSheet,§8.3):列目标底部 sheet + 列内排序;
 * - 移动紧凑(BoardCompact,§8.1/§8.3):compact 视口(≤599px)单泳道 + chips 切列;
 * - 虚拟化(VirtualColumnBody,§11.4):列内 ≥200 卡片仅渲染可见窗口。
 *
 * a11y 模型:列体 role="list",卡片 role="listitem"(aria-roledescription 标注可拖拽、
 * aria-keyshortcuts 暴露键盘序列);拖拽提供等价的键盘/触摸替代路径(§10.2);
 * 拖拽各阶段经 aria-live assertive 区域(board-live)播报。
 */
/* eslint-disable react-refresh/only-export-components -- 纯工具与列组件同模块契约 */
import { useCallback, useMemo, useRef, useState } from 'react';
import { Icon } from '../../design';
import { useT } from '../../i18n';
import type { TranslateFn } from '../../i18n';
import { BoardCompact, useIsCompactViewport } from './BoardCompact';
import { BoardDragLayer } from './BoardDragLayer';
import { BoardTouchMoveSheet } from './BoardTouchMoveSheet';
import { VirtualColumnBody, shouldVirtualize } from './VirtualColumnBody';
import type { VirtualItemA11y } from './VirtualColumnBody';
import { useBoardDrag } from './useBoardDrag';
import type { DragState } from './useBoardDrag';
import { useBoardKeyboardMove } from './useBoardKeyboardMove';
import type { KeyboardMoveState } from './useBoardKeyboardMove';
import type { CardRect, ColumnRect } from './dragGeometry';
import type { BoardCard } from './projection';
import type { BoardColumn } from './types';
import './board.css';
import './board-drag.css';

/** 状态类别的语义色 token(经 CSS 变量引用,禁硬编码色值,§6.12)。 */
export function categoryColorClass(key: string): string {
  return `mesh-board__dot--${key}`;
}

/**
 * 浮点中点法定位(kanban §4.3):插入 index 处取相邻中点;列顶 = 首张 -1;
 * 列底/空列 = 末张 +1(空列 = 1)。
 */
export function computeDropPosition(cards: readonly BoardCard[], index: number | null): number {
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

/** 列展示标签(动态分组直用服务端 label;类别/优先级走 i18n)。 */
function resolveColumnLabel(column: BoardColumn, groupBy: string | null, t: TranslateFn): string {
  const isDynamic = groupBy !== null && groupBy !== 'state_category' && groupBy !== 'priority';
  if (column.key === '__dynamic__') {
    return t('board.dynamicColumnsPlaceholder', { groupBy: groupBy ?? '' });
  }
  if (isDynamic) return column.label;
  return t(column.label);
}

/** WIP 徽章:count/limit 文案 + title 提示恒在;超限附 warning 图标(非仅颜色,§13.2)。 */
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
      {exceeded ? <Icon name="warning" size={16} /> : null}
      {column.count}/{column.wip.limit}
    </span>
  );
}

/** 拖拽预检 WIP 提示条(warn 放行 / block 禁落,图标+文字非仅颜色,§9.4.3)。 */
function WipStrip({
  columnKey,
  tone,
}: {
  columnKey: string;
  tone: 'warn' | 'block';
}): React.JSX.Element {
  const t = useT();
  return (
    <div
      className={`mesh-board__wip-strip mesh-board__wip-strip--${tone}`}
      data-testid={`board-wip-strip-${columnKey}`}
      role="status"
    >
      <Icon name="warning" size={16} />
      <span>{t(tone === 'block' ? 'board.wipDragBlock' : 'board.wipDragWarn')}</span>
    </div>
  );
}

function QuickCreate({
  groupKey,
  canWrite,
  onQuickCreate,
}: {
  groupKey: string;
  canWrite: boolean;
  onQuickCreate: (groupKey: string, title: string) => void | Promise<void>;
}): React.JSX.Element {
  const t = useT();
  const [title, setTitle] = useState('');
  const [pending, setPending] = useState(false);
  // 保留 reload 路径(BoardPage 创建后整板重拉):实现更简、失败反馈已由 BoardPage
  // toast 覆盖;此处仅呈现内联 pending(禁用 + spinner),不做乐观临时卡。
  const submit = (): void => {
    const trimmed = title.trim();
    if (trimmed === '' || pending) return;
    setPending(true);
    setTitle('');
    void Promise.resolve(onQuickCreate(groupKey, trimmed)).finally(() => setPending(false));
  };
  return (
    <div className="mesh-board__quick-create">
      <input
        className="mesh-board__quick-create-input"
        placeholder={t('board.quickAdd')}
        value={title}
        disabled={!canWrite || pending}
        aria-label={t('board.quickAdd')}
        data-testid={`quick-add-${groupKey}`}
        onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => {
          // §9.3.3:Enter / Cmd|Ctrl+Enter 提交,Esc 清空(有内容时即「关闭」)。
          if (event.key === 'Enter') {
            event.preventDefault();
            submit();
          } else if (event.key === 'Escape') {
            setTitle('');
          }
        }}
      />
      {pending ? (
        <span
          className="mesh-board__quick-create-spinner"
          data-testid={`quick-add-pending-${groupKey}`}
          role="status"
          aria-label={t('common.loading')}
        />
      ) : null}
    </div>
  );
}

interface BoardCardItemProps {
  readonly card: BoardCard;
  readonly columnKey: string;
  readonly isPlaceholder: boolean;
  readonly isSelected: boolean;
  /** 创建成功后 1.2s 插入高亮(§9.3.4)。 */
  readonly isHighlighted: boolean;
  /** 虚拟化窗口的 AT 坐标(仅虚拟化路径提供;§10.2 不破坏读屏集合语义)。 */
  readonly virtualSetSize?: number;
  readonly virtualPosInSet?: number;
  readonly onCardPointerDown: (
    event: React.PointerEvent,
    cardId: string,
    identifier: string,
  ) => void;
  readonly onCardKeyDown: (
    event: React.KeyboardEvent,
    cardId: string,
    identifier: string,
    columnKey: string,
  ) => void;
}

function BoardCardItem(props: BoardCardItemProps): React.JSX.Element {
  const {
    card,
    columnKey,
    isPlaceholder,
    isSelected,
    isHighlighted,
    virtualSetSize,
    virtualPosInSet,
    onCardPointerDown,
    onCardKeyDown,
  } = props;
  const className = [
    'mesh-board__card',
    isPlaceholder ? 'mesh-board__card--placeholder' : '',
    isSelected ? 'mesh-board__card--selected' : '',
    isHighlighted ? 'mesh-board__card--highlight' : '',
  ]
    .filter((part) => part !== '')
    .join(' ');
  return (
    <div
      className={className}
      data-testid={`board-card-${card.id}`}
      role="listitem"
      tabIndex={0}
      aria-roledescription="draggable card"
      aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight Enter Escape"
      aria-setsize={virtualSetSize}
      aria-posinset={virtualPosInSet}
      onPointerDown={(event) => onCardPointerDown(event, card.id, card.identifier)}
      onKeyDown={(event) => onCardKeyDown(event, card.id, card.identifier, columnKey)}
    >
      <span className="mesh-board__card-grip" aria-hidden="true">
        <Icon name="grip" size={16} />
      </span>
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

/**
 * 非虚拟化路径的卡片渲染:落点指示线按 hit.index 插入卡片之间(§9.4.2 插入位
 * 反馈;index 为 null 时置于列尾)。虚拟化路径由 VirtualColumnBody 内部绝对定位。
 */
function renderCardsWithIndicator(
  cards: readonly BoardCard[],
  showIndicator: boolean,
  indicatorIndex: number | null,
  renderCard: (card: BoardCard) => React.JSX.Element,
): React.ReactNode[] {
  const nodes: React.ReactNode[] = cards.map((card) => renderCard(card));
  if (!showIndicator) return nodes;
  const indicator = (
    <div
      key="__drop-indicator"
      className="mesh-board__drop-indicator"
      data-testid="board-drop-indicator"
      aria-hidden="true"
    />
  );
  if (indicatorIndex === null || indicatorIndex >= nodes.length) {
    nodes.push(indicator);
  } else {
    nodes.splice(Math.max(indicatorIndex, 0), 0, indicator);
  }
  return nodes;
}

interface BoardColumnCardProps {
  readonly column: BoardColumn;
  readonly label: string;
  readonly cards: readonly BoardCard[];
  readonly canWrite: boolean;
  readonly dragState: DragState | null;
  readonly moveState: KeyboardMoveState | null;
  readonly onToggleCollapse: (key: string) => void;
  readonly onQuickCreate: (groupKey: string, title: string) => void | Promise<void>;
  readonly highlightCardId: string | null;
  readonly onCardPointerDown: BoardCardItemProps['onCardPointerDown'];
  readonly onCardKeyDown: BoardCardItemProps['onCardKeyDown'];
}

function BoardColumnCard(props: BoardColumnCardProps): React.JSX.Element {
  const {
    column,
    label,
    cards,
    canWrite,
    dragState,
    moveState,
    onToggleCollapse,
    onQuickCreate,
    highlightCardId,
    onCardPointerDown,
    onCardKeyDown,
  } = props;
  const t = useT();

  // 拖拽悬停目标列 → 高亮;命中且未被 WIP block → 呈现落点指示线。
  // 回位动画阶段(returning)不再呈现目标列反馈,仅浮层滑回源卡(§9.4.4)。
  const isDragTarget =
    dragState !== null && dragState.returning !== true && dragState.hit?.columnKey === column.key;
  const showIndicator = isDragTarget && dragState !== null && !dragState.isBlocked;
  const isMoveTarget = moveState !== null && moveState.targetColumnKey === column.key;
  const stripTone: 'warn' | 'block' | null =
    isDragTarget && dragState !== null
      ? dragState.isBlocked
        ? 'block'
        : dragState.isWarn
          ? 'warn'
          : null
      : null;
  const columnClassName = [
    'mesh-board__column',
    isDragTarget ? 'mesh-board__column--drag-over' : '',
    isMoveTarget ? 'mesh-board__column--move-target' : '',
  ]
    .filter((part) => part !== '')
    .join(' ');
  // WIP block 满载视觉提示(§4.4)。真正的硬阻止由服务端在 /moves 事务内强制
  // (422 → 弹回 + toast);拖拽预检仅提示,指针拖拽在 block 列禁落(§9.4.3)。
  const wipFull =
    column.wip !== null && column.wip.enforcement === 'block' && column.count >= column.wip.limit;

  const renderCard = (card: BoardCard, virtualA11y?: VirtualItemA11y): React.JSX.Element => (
    <BoardCardItem
      key={card.id}
      card={card}
      columnKey={column.key}
      isPlaceholder={dragState?.cardId === card.id}
      isSelected={moveState?.cardId === card.id}
      isHighlighted={highlightCardId === card.id}
      virtualSetSize={virtualA11y?.setsize}
      virtualPosInSet={virtualA11y?.posinset}
      onCardPointerDown={onCardPointerDown}
      onCardKeyDown={onCardKeyDown}
    />
  );

  return (
    <section
      className={columnClassName}
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
          <Icon name={column.collapsed ? 'chevron-right' : 'chevron-down'} size={16} />
        </button>
      </header>
      {column.collapsed ? null : (
        <div
          className={`mesh-board__column-body ${wipFull ? 'mesh-board__column-body--blocked' : ''}`.trim()}
          data-testid={`column-body-${column.key}`}
        >
          {stripTone !== null ? <WipStrip columnKey={column.key} tone={stripTone} /> : null}
          {cards.length === 0 ? (
            <>
              {showIndicator ? (
                <div
                  className="mesh-board__drop-indicator"
                  data-testid="board-drop-indicator"
                  aria-hidden="true"
                />
              ) : null}
              <p className="mesh-board__column-empty">{t('board.columnEmptyTitle')}</p>
            </>
          ) : shouldVirtualize(cards.length) ? (
            <VirtualColumnBody
              cards={cards}
              activeCardId={moveState?.cardId ?? null}
              renderCard={(card, _index, virtualA11y) => renderCard(card as BoardCard, virtualA11y)}
              indicatorNode={
                showIndicator ? (
                  <div
                    className="mesh-board__drop-indicator"
                    data-testid="board-drop-indicator"
                    aria-hidden="true"
                  />
                ) : undefined
              }
              indicatorIndex={showIndicator ? (dragState?.hit?.index ?? null) : undefined}
            />
          ) : (
            <div role="list" className="mesh-board__card-list">
              {renderCardsWithIndicator(
                cards,
                showIndicator,
                dragState?.hit?.index ?? null,
                renderCard,
              )}
            </div>
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
  readonly onQuickCreate: (groupKey: string, title: string) => void | Promise<void>;
  /** 新建卡片 1.2s 插入高亮(§9.3.4);缺省无高亮。 */
  readonly highlightCardId?: string | null;
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
    highlightCardId,
  } = props;
  const t = useT();
  const boardRef = useRef<HTMLDivElement>(null);
  // 形态切换基准为视口模式(§8.1 模式表 compact = 0–599px),matchMedia 即时
  // 可得且稳定,杜绝容器宽度测量在负载下的时序抖动(验收第 3 轮打回根因)。
  const isCompact = useIsCompactViewport();
  const [compactIndex, setCompactIndex] = useState(0);
  const [touchCardId, setTouchCardId] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const announce = useCallback((message: string) => setAnnouncement(message), []);

  const columnLabelByKey = useMemo(() => {
    const map: Record<string, string> = {};
    for (const column of columns) map[column.key] = resolveColumnLabel(column, groupBy, t);
    return map;
  }, [columns, groupBy, t]);
  const getColumnLabel = useCallback(
    (key: string) => columnLabelByKey[key] ?? key,
    [columnLabelByKey],
  );
  const columnKeys = useMemo(() => columns.map((column) => column.key), [columns]);

  const computePosition = useCallback(
    (columnKey: string, index: number | null) =>
      computeDropPosition(cardsByKey[columnKey] ?? [], index),
    [cardsByKey],
  );
  const getCardCount = useCallback(
    (columnKey: string) => (cardsByKey[columnKey] ?? []).length,
    [cardsByKey],
  );
  const findColumn = useCallback(
    (key: string) => columns.find((column) => column.key === key),
    [columns],
  );
  const isColumnBlocked = useCallback(
    (key: string) => {
      const column = findColumn(key);
      return (
        column !== undefined &&
        column.wip !== null &&
        column.wip.enforcement === 'block' &&
        column.count >= column.wip.limit
      );
    },
    [findColumn],
  );
  const isColumnWarn = useCallback(
    (key: string) => {
      const column = findColumn(key);
      return (
        column !== undefined &&
        column.wip !== null &&
        column.wip.enforcement === 'warn' &&
        column.count >= column.wip.limit
      );
    },
    [findColumn],
  );

  // 几何命中检测:从已渲染列/卡片测量矩形(测试经 getBoundingClientRect mock)。
  const getColumnRects = useCallback((): readonly ColumnRect[] => {
    const root = boardRef.current;
    if (root === null) return [];
    const elements = root.querySelectorAll<HTMLElement>('[data-testid^="board-column-"]');
    return [...elements].map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        columnKey: element.dataset.testid?.replace('board-column-', '') ?? '',
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
      };
    });
  }, []);
  const getCardRects = useCallback((columnKey: string): readonly CardRect[] => {
    const root = boardRef.current;
    if (root === null) return [];
    const body = root.querySelector<HTMLElement>(`[data-testid="column-body-${columnKey}"]`);
    if (body === null) return [];
    const cardElements = body.querySelectorAll<HTMLElement>('[data-testid^="board-card-"]');
    return [...cardElements].map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        cardId: element.dataset.testid?.replace('board-card-', '') ?? '',
        top: rect.top,
        bottom: rect.bottom,
      };
    });
  }, []);

  const onLongPress = useCallback((cardId: string) => setTouchCardId(cardId), []);
  const drag = useBoardDrag(
    dragEnabled,
    {
      onDropCard,
      computePosition,
      isColumnBlocked,
      isColumnWarn,
      getColumnLabel,
      announce,
      t,
      onLongPress,
    },
    getColumnRects,
    getCardRects,
  );
  const keyboard = useBoardKeyboardMove({
    enabled: dragEnabled,
    columns: columnKeys,
    getCardCount,
    getColumnLabel,
    onDropCard,
    computePosition,
    announce,
    t,
  });

  const touchCard = useMemo(() => {
    if (touchCardId === null) return null;
    for (const group of Object.values(cardsByKey)) {
      const found = group.find((card) => card.id === touchCardId);
      if (found !== undefined) return found;
    }
    return null;
  }, [touchCardId, cardsByKey]);

  const renderColumn = (column: BoardColumn): React.JSX.Element => (
    <BoardColumnCard
      key={column.key}
      column={column}
      label={getColumnLabel(column.key)}
      cards={cardsByKey[column.key] ?? []}
      canWrite={canWrite}
      dragState={drag.dragState}
      moveState={keyboard.moveState}
      onToggleCollapse={onToggleCollapse}
      onQuickCreate={onQuickCreate}
      highlightCardId={highlightCardId ?? null}
      onCardPointerDown={drag.onPointerDown}
      onCardKeyDown={keyboard.handleCardKeyDown}
    />
  );

  const activeCompactIndex = columns.length === 0 ? 0 : compactIndex % columns.length;

  return (
    <div className="mesh-board__columns-wrap" ref={boardRef} data-testid="board-columns-wrap">
      {/* aria-live 播报区(视觉隐藏,复用 design/base.css .sr-only):拖拽/键盘移动各阶段,§10.2。 */}
      <div className="sr-only" aria-live="assertive" data-testid="board-live">
        {announcement}
      </div>
      {drag.dragState !== null ? <BoardDragLayer dragState={drag.dragState} /> : null}
      {isCompact ? (
        <BoardCompact
          columns={columns}
          cardsByKey={cardsByKey}
          activeIndex={activeCompactIndex}
          onSelectIndex={setCompactIndex}
          getColumnLabel={getColumnLabel}
          renderCardBody={(column) => renderColumn(column)}
        />
      ) : (
        <div className="mesh-board__columns" data-testid="board-columns">
          {columns.map((column) => renderColumn(column))}
        </div>
      )}
      {touchCard !== null ? (
        <BoardTouchMoveSheet
          card={touchCard}
          columns={columns}
          cardsByKey={cardsByKey}
          computePosition={computePosition}
          onDropCard={onDropCard}
          onClose={() => setTouchCardId(null)}
          announce={announce}
          getColumnLabel={getColumnLabel}
        />
      ) : null}
    </div>
  );
}
