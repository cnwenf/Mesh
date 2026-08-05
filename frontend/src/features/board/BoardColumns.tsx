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
 * a11y 模型:列体 role="list",卡片 role="listitem"(aria-keyshortcuts 暴露键盘
 * 序列);拖拽提供等价的键盘/触摸替代路径(§10.2);
 * 拖拽各阶段经 aria-live assertive 区域(board-live)播报。
 */
/* eslint-disable react-refresh/only-export-components -- 纯工具与列组件同模块契约 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useIntl } from 'react-intl';
import { Button, Icon, IconButton, Input } from '../../design';
import { formatDate, formatNumber, useT } from '../../i18n';
import type { TranslateFn } from '../../i18n';
import { useShortcutRegistry } from '../../shortcuts';
import { useSettingsStore } from '../../state/settingsStore';
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

const FIXED_COLUMN_TONES = new Set([
  'backlog',
  'todo',
  'in_progress',
  'in_review',
  'blocked',
  'done',
  'cancelled',
  'urgent',
  'high',
  'medium',
  'low',
  'none',
]);
const FIXED_PRIORITY_TONES = new Set(['urgent', 'high', 'medium', 'low', 'none']);

/** 固定状态/优先级列使用语义淡色面；服务端动态实体保持中性。 */
export function columnToneClass(key: string, groupBy: string | null): string {
  const isFixedGroup = groupBy === null || groupBy === 'state_category' || groupBy === 'priority';
  return isFixedGroup && FIXED_COLUMN_TONES.has(key)
    ? `mesh-board__column--${key}`
    : 'mesh-board__column--neutral';
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
  subGroupKey,
  testKey,
  canWrite,
  onQuickCreate,
}: {
  groupKey: string;
  subGroupKey?: string;
  testKey: string;
  canWrite: boolean;
  onQuickCreate: (groupKey: string, title: string, subGroupKey?: string) => void | Promise<void>;
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
    const result =
      subGroupKey === undefined
        ? onQuickCreate(groupKey, trimmed)
        : onQuickCreate(groupKey, trimmed, subGroupKey);
    void Promise.resolve(result).finally(() => setPending(false));
  };
  return (
    <div className="mesh-board__quick-create">
      <Input
        label={t('board.quickAdd')}
        className="mesh-board__quick-create-input"
        placeholder={t('board.quickAdd')}
        value={title}
        disabled={!canWrite || pending}
        data-testid={`quick-add-${testKey}`}
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
          data-testid={`quick-add-pending-${testKey}`}
          role="status"
          aria-label={t('common.loading')}
        />
      ) : null}
    </div>
  );
}

interface BoardCardItemProps {
  readonly card: BoardCard;
  readonly cardFields: readonly string[];
  readonly columnKey: string;
  readonly isPlaceholder: boolean;
  readonly isSelected: boolean;
  /** 创建成功后 1.2s 插入高亮(§9.3.4)。 */
  readonly isHighlighted: boolean;
  /** 虚拟化窗口的 AT 坐标(仅虚拟化路径提供;§10.2 不破坏读屏集合语义)。 */
  readonly virtualSetSize?: number;
  readonly virtualPosInSet?: number;
  /** 当前逻辑执行状态；仅活跃态会由父级传入。 */
  readonly executionStatus?: string;
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
  readonly onSelect: (cardId: string, columnKey: string) => void;
}

function BoardCardItem(props: BoardCardItemProps): React.JSX.Element {
  const {
    card,
    cardFields,
    columnKey,
    isPlaceholder,
    isSelected,
    isHighlighted,
    virtualSetSize,
    virtualPosInSet,
    executionStatus,
    onCardPointerDown,
    onCardKeyDown,
    onSelect,
  } = props;
  const intl = useIntl();
  const t = useT();
  const timeZone = useSettingsStore((state) => state.preferences.timezone);
  const className = [
    'mesh-board__card',
    isPlaceholder ? 'mesh-board__card--placeholder' : '',
    isSelected ? 'mesh-board__card--selected' : '',
    isHighlighted ? 'mesh-board__card--highlight' : '',
  ]
    .filter((part) => part !== '')
    .join(' ');
  const visible = new Set(cardFields);
  const priorityLabel = FIXED_PRIORITY_TONES.has(card.priority)
    ? t(`board.priority.${card.priority}`)
    : card.priority;
  const estimateUnit =
    card.estimate_unit === 'points' || card.estimate_unit === 'hours'
      ? t(`issues.detail.estimateUnit.${card.estimate_unit}`)
      : card.estimate_unit;
  // due_date 是数据库 DATE 而非瞬时，固定 UTC 解析以免负时区把日历日倒退一天；
  // updated_at 是 UTC 瞬时，按账号时区转换。
  const dueDate = card.due_date
    ? formatDate(`${card.due_date}T00:00:00Z`, { locale: intl.locale, timeZone: 'UTC' })
    : null;
  const updatedDate =
    card.updated_at === '' ? null : formatDate(card.updated_at, { locale: intl.locale, timeZone });
  return (
    <div
      className={className}
      data-testid={`board-card-${card.id}`}
      role="listitem"
      tabIndex={0}
      aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight J K H L C S A F Enter Escape"
      aria-current={isSelected ? 'true' : undefined}
      aria-setsize={virtualSetSize}
      aria-posinset={virtualPosInSet}
      onPointerDown={(event) => {
        onSelect(card.id, columnKey);
        onCardPointerDown(event, card.id, card.identifier);
      }}
      onFocus={() => onSelect(card.id, columnKey)}
      onKeyDown={(event) => {
        onCardKeyDown(event, card.id, card.identifier, columnKey);
        // 方向键移动模式会 preventDefault；此时连同阻断冒泡，避免同一
        // Enter 又被 window 层 board.open 解读为跳转详情。未进入移动模式的
        // Enter 不会被 preventDefault，仍保留“打开卡片”快捷键。
        if (event.defaultPrevented) event.stopPropagation();
      }}
    >
      <div className="mesh-board__card-head">
        <span className="mesh-board__card-grip" aria-hidden="true">
          <Icon name="grip" size={16} />
        </span>
        <span className="mesh-board__card-id">{card.identifier}</span>
        <span className={`mesh-board__card-priority mesh-board__card-priority--${card.priority}`}>
          {priorityLabel}
        </span>
      </div>
      <span className="mesh-board__card-title">{card.title}</span>
      {executionStatus !== undefined ? (
        <span
          className="mesh-board__card-execution"
          data-testid={`board-card-execution-${card.id}`}
          role="status"
          data-execution-status={executionStatus}
        >
          <span className="mesh-board__card-execution-dot" aria-hidden="true" />
          {t('board.issueProcessing')}
        </span>
      ) : null}
      {visible.has('description') && card.description ? (
        <span className="mesh-board__card-description">{card.description}</span>
      ) : null}
      <div className="mesh-board__card-meta">
        {visible.has('project') && card.project !== null && card.project !== undefined ? (
          <span className="mesh-board__card-project">{card.project.name}</span>
        ) : null}
        {visible.has('estimate') && card.estimate !== null && card.estimate !== undefined ? (
          <span>{`${formatNumber(card.estimate, { locale: intl.locale })}${estimateUnit ? ` ${estimateUnit}` : ''}`}</span>
        ) : null}
        {visible.has('due_date') && dueDate !== null ? <span>{dueDate}</span> : null}
      </div>
      <div className="mesh-board__card-footer">
        {visible.has('assignee') && card.assignee !== null ? (
          <span className="mesh-board__card-assignee" title={card.assignee.name}>
            {card.assignee.name}
          </span>
        ) : null}
        {visible.has('updated_at') && updatedDate !== null ? (
          <time dateTime={card.updated_at}>{updatedDate}</time>
        ) : null}
      </div>
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
  readonly cardFields: readonly string[];
  readonly canWrite: boolean;
  readonly dragState: DragState | null;
  readonly moveState: KeyboardMoveState | null;
  readonly onToggleCollapse: (key: string) => void;
  readonly onQuickCreate: (
    groupKey: string,
    title: string,
    subGroupKey?: string,
  ) => void | Promise<void>;
  readonly highlightCardId: string | null;
  readonly onCardPointerDown: BoardCardItemProps['onCardPointerDown'];
  readonly onCardKeyDown: BoardCardItemProps['onCardKeyDown'];
  readonly onSelectCard: BoardCardItemProps['onSelect'];
  readonly selectedCardId: string | null;
  readonly fullListMode: boolean;
  readonly dropKey: string;
  readonly testKey: string;
  readonly subGroupKey?: string;
  readonly toneClass: string;
  readonly executionStatusByIssueId: Readonly<Record<string, string>>;
}

function BoardColumnCard(props: BoardColumnCardProps): React.JSX.Element {
  const {
    column,
    label,
    cards,
    cardFields,
    canWrite,
    dragState,
    moveState,
    onToggleCollapse,
    onQuickCreate,
    highlightCardId,
    onCardPointerDown,
    onCardKeyDown,
    onSelectCard,
    selectedCardId,
    fullListMode,
    dropKey,
    testKey,
    subGroupKey,
    toneClass,
    executionStatusByIssueId,
  } = props;
  const t = useT();
  const headingId = useId();

  // 拖拽悬停目标列 → 高亮;命中且未被 WIP block → 呈现落点指示线。
  // 回位动画阶段(returning)不再呈现目标列反馈,仅浮层滑回源卡(§9.4.4)。
  const isDragTarget =
    dragState !== null && dragState.returning !== true && dragState.hit?.columnKey === dropKey;
  const showIndicator = isDragTarget && dragState !== null && !dragState.isBlocked;
  const isMoveTarget = moveState !== null && moveState.targetColumnKey === dropKey;
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
    toneClass,
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
      cardFields={cardFields}
      columnKey={dropKey}
      isPlaceholder={dragState?.cardId === card.id}
      isSelected={moveState?.cardId === card.id || selectedCardId === card.id}
      isHighlighted={highlightCardId === card.id}
      virtualSetSize={virtualA11y?.setsize}
      virtualPosInSet={virtualA11y?.posinset}
      executionStatus={executionStatusByIssueId[card.id]}
      onCardPointerDown={onCardPointerDown}
      onCardKeyDown={onCardKeyDown}
      onSelect={onSelectCard}
    />
  );

  return (
    <section
      className={columnClassName}
      data-testid={`board-column-${testKey}`}
      data-board-drop-key={dropKey}
      aria-labelledby={headingId}
    >
      <header className="mesh-board__column-head">
        <span className={`mesh-board__dot ${categoryColorClass(column.key)}`} aria-hidden="true" />
        <h2 id={headingId} className="mesh-board__column-name">
          {label}
        </h2>
        <span className="mesh-board__count" data-testid={`count-${testKey}`}>
          {column.count}
        </span>
        <WipBadge column={column} />
        <IconButton
          variant="ghost"
          size="sm"
          className="mesh-board__collapse"
          aria-expanded={!column.collapsed}
          label={t(column.collapsed ? 'board.expandColumn' : 'board.collapseColumn', {
            name: label,
          })}
          onClick={() => onToggleCollapse(column.key)}
        >
          <Icon name={column.collapsed ? 'chevron-right' : 'chevron-down'} size={16} />
        </IconButton>
      </header>
      {column.collapsed ? null : (
        <div
          className={`mesh-board__column-body ${wipFull ? 'mesh-board__column-body--blocked' : ''}`.trim()}
          data-testid={`column-body-${testKey}`}
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
          ) : !fullListMode && shouldVirtualize(cards.length) ? (
            <VirtualColumnBody
              cards={cards}
              activeCardId={moveState?.cardId ?? selectedCardId}
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
          <QuickCreate
            groupKey={column.key}
            subGroupKey={subGroupKey}
            testKey={testKey}
            canWrite={canWrite}
            onQuickCreate={onQuickCreate}
          />
        </div>
      )}
    </section>
  );
}

export interface BoardDropTarget {
  /** 同一视图内唯一的 cell key，供拖拽命中与键盘移动使用。 */
  readonly key: string;
  readonly groupKey: string;
  readonly subGroupKey: string;
  readonly label: string;
  readonly column: BoardColumn;
  readonly cards: readonly BoardCard[];
}

interface BoardColumnsProps {
  readonly columns: readonly BoardColumn[];
  readonly groupBy: string | null;
  readonly cardsByKey: Readonly<Record<string, readonly BoardCard[]>>;
  readonly cardFields?: readonly string[];
  readonly canWrite: boolean;
  readonly dragEnabled: boolean;
  readonly onToggleCollapse: (key: string) => void;
  readonly onDropCard: (
    issueId: string,
    toGroupKey: string,
    position: number,
    toSubGroupKey?: string,
  ) => void;
  readonly onQuickCreate: (
    groupKey: string,
    title: string,
    subGroupKey?: string,
  ) => void | Promise<void>;
  /** 二维泳道中当前行的 key；一维时不传以保持旧 DOM 契约。 */
  readonly subGroupKey?: string;
  /** 二维模式的全板 cell 集，使单个泳道内起拖可命中其他泳道。 */
  readonly dropTargets?: readonly BoardDropTarget[];
  /** 多泳道共享的视觉拖拽态，使非源 lane 也能显示命中反馈。 */
  readonly sharedDragState?: DragState | null;
  readonly onDragStateChange?: (state: DragState | null) => void;
  /** 新建卡片 1.2s 插入高亮(§9.3.4);缺省无高亮。 */
  readonly highlightCardId?: string | null;
  /** issue id → 活跃 execution 状态；终态不应进入此映射。 */
  readonly executionStatusByIssueId?: Readonly<Record<string, string>>;
}

interface BoardSelection {
  readonly cardId: string;
  readonly columnKey: string;
  readonly index: number;
}

export function BoardColumns(props: BoardColumnsProps): React.JSX.Element {
  const {
    columns,
    groupBy,
    cardsByKey,
    cardFields = ['description', 'project', 'estimate', 'due_date', 'assignee', 'updated_at'],
    canWrite,
    dragEnabled,
    onToggleCollapse,
    onDropCard,
    onQuickCreate,
    highlightCardId,
    subGroupKey,
    dropTargets,
    sharedDragState,
    onDragStateChange,
    executionStatusByIssueId = {},
  } = props;
  const t = useT();
  const boardRef = useRef<HTMLDivElement>(null);
  // 形态切换基准为视口模式(§8.1 模式表 compact = 0–599px),matchMedia 即时
  // 可得且稳定,杜绝容器宽度测量在负载下的时序抖动(验收第 3 轮打回根因)。
  const isCompact = useIsCompactViewport();
  const [compactIndex, setCompactIndex] = useState(0);
  const [touchCardId, setTouchCardId] = useState<string | null>(null);
  const [selection, setSelection] = useState<BoardSelection | null>(null);
  const [fullListMode, setFullListMode] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const announce = useCallback((message: string) => setAnnouncement(message), []);

  const columnLabelByKey = useMemo(() => {
    const map: Record<string, string> = {};
    for (const column of columns) map[column.key] = resolveColumnLabel(column, groupBy, t);
    for (const target of dropTargets ?? []) map[target.key] = target.label;
    return map;
  }, [columns, dropTargets, groupBy, t]);
  const getColumnLabel = useCallback(
    (key: string) => columnLabelByKey[key] ?? key,
    [columnLabelByKey],
  );
  const columnKeys = useMemo(
    () => dropTargets?.map((target) => target.key) ?? columns.map((column) => column.key),
    [columns, dropTargets],
  );
  const effectiveCardsByKey = useMemo<Readonly<Record<string, readonly BoardCard[]>>>(() => {
    if (dropTargets === undefined) return cardsByKey;
    return Object.fromEntries(dropTargets.map((target) => [target.key, target.cards]));
  }, [cardsByKey, dropTargets]);
  const currentDropKey = useCallback(
    (groupKey: string) =>
      dropTargets?.find(
        (target) => target.groupKey === groupKey && target.subGroupKey === subGroupKey,
      )?.key ?? groupKey,
    [dropTargets, subGroupKey],
  );
  const firstSelection = useCallback((): BoardSelection | null => {
    for (const column of columns) {
      const dropKey = currentDropKey(column.key);
      const first = effectiveCardsByKey[dropKey]?.[0];
      if (first !== undefined) return { cardId: first.id, columnKey: dropKey, index: 0 };
    }
    return null;
  }, [columns, currentDropKey, effectiveCardsByKey]);

  const selectCard = useCallback(
    (cardId: string, columnKey: string) => {
      const index = (effectiveCardsByKey[columnKey] ?? []).findIndex((card) => card.id === cardId);
      if (index >= 0) setSelection({ cardId, columnKey, index });
    },
    [effectiveCardsByKey],
  );

  // 选中态变化后把真实 DOM 焦点送到卡片；虚拟窗口通过 activeCardId 保证该卡挂载。
  useEffect(() => {
    if (selection === null) return;
    boardRef.current
      ?.querySelector<HTMLElement>(`[data-testid="board-card-${CSS.escape(selection.cardId)}"]`)
      ?.focus();
  }, [selection]);

  // 数据刷新移除当前卡时回到首张，避免快捷键引用陈旧 id。
  useEffect(() => {
    if (selection === null) return;
    const exists = (effectiveCardsByKey[selection.columnKey] ?? []).some(
      (card) => card.id === selection.cardId,
    );
    if (!exists) setSelection(firstSelection());
  }, [effectiveCardsByKey, selection, firstSelection]);

  // search-command-palette.md §4.3:BoardPage owns the keyboard handlers and page context.
  // Palette commands delegate to those exact registered actions, avoiding a second business path.
  useEffect(() => {
    const registry = useShortcutRegistry.getState();
    const command = (id: string, label: string) =>
      registry.registerCommand({
        id,
        label,
        group: 'board',
        run: () => registry.shortcuts.find((shortcut) => shortcut.id === id)?.run(),
      });
    const unregisterCommands = [
      command('board.move.up.vim', t('shortcuts.boardPrevious')),
      command('board.move.down.vim', t('shortcuts.boardNext')),
      command('board.move.left.vim', t('shortcuts.boardLeft')),
      command('board.move.right.vim', t('shortcuts.boardRight')),
      command('board.new.card', t('shortcuts.boardCreate')),
      command('board.change.status', t('shortcuts.boardStatus')),
      command('board.change.assignee', t('shortcuts.boardAssignee')),
      command('board.open.card', t('shortcuts.boardOpen')),
      command('board.filter', t('shortcuts.boardFilter')),
    ];
    return () => {
      for (const unregister of unregisterCommands) unregister();
    };
  }, [t]);

  const computePosition = useCallback(
    (columnKey: string, index: number | null) =>
      computeDropPosition(effectiveCardsByKey[columnKey] ?? [], index),
    [effectiveCardsByKey],
  );
  const getCardCount = useCallback(
    (columnKey: string) => (effectiveCardsByKey[columnKey] ?? []).length,
    [effectiveCardsByKey],
  );
  const findColumn = useCallback(
    (key: string) =>
      dropTargets?.find((target) => target.key === key)?.column ??
      columns.find((column) => column.key === key),
    [columns, dropTargets],
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
    const scope = dropTargets === undefined ? root : root.closest('[data-board-drag-scope]');
    if (scope === null) return [];
    const elements = scope.querySelectorAll<HTMLElement>('[data-board-drop-key]');
    return [...elements].map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        columnKey: element.dataset.boardDropKey ?? '',
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
      };
    });
  }, [dropTargets]);
  const getCardRects = useCallback(
    (columnKey: string): readonly CardRect[] => {
      const root = boardRef.current;
      if (root === null) return [];
      const scope = dropTargets === undefined ? root : root.closest('[data-board-drag-scope]');
      if (scope === null) return [];
      const column = [...scope.querySelectorAll<HTMLElement>('[data-board-drop-key]')].find(
        (element) => element.dataset.boardDropKey === columnKey,
      );
      const body = column?.querySelector<HTMLElement>('.mesh-board__column-body') ?? null;
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
    },
    [dropTargets],
  );

  const dropCard = useCallback(
    (issueId: string, targetKey: string, position: number) => {
      const target = dropTargets?.find((candidate) => candidate.key === targetKey);
      const targetSubGroupKey = target?.subGroupKey ?? subGroupKey;
      if (targetSubGroupKey === undefined) {
        onDropCard(issueId, target?.groupKey ?? targetKey, position);
      } else {
        onDropCard(issueId, target?.groupKey ?? targetKey, position, targetSubGroupKey);
      }
    },
    [dropTargets, onDropCard, subGroupKey],
  );

  const onLongPress = useCallback((cardId: string) => setTouchCardId(cardId), []);
  const drag = useBoardDrag(
    dragEnabled,
    {
      onDropCard: dropCard,
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
  useEffect(() => {
    onDragStateChange?.(drag.dragState);
  }, [drag.dragState, onDragStateChange]);
  const visualDragState = drag.dragState ?? sharedDragState ?? null;
  const keyboard = useBoardKeyboardMove({
    enabled: dragEnabled,
    columns: columnKeys,
    getCardCount,
    getColumnLabel,
    onDropCard: dropCard,
    computePosition,
    announce,
    t,
  });

  const touchCard = useMemo(() => {
    if (touchCardId === null) return null;
    for (const group of Object.values(effectiveCardsByKey)) {
      const found = group.find((card) => card.id === touchCardId);
      if (found !== undefined) return found;
    }
    return null;
  }, [touchCardId, effectiveCardsByKey]);

  const renderColumn = (column: BoardColumn): React.JSX.Element => {
    const dropKey = currentDropKey(column.key);
    const testKey = subGroupKey === undefined ? column.key : `${subGroupKey}-${column.key}`;
    return (
      <BoardColumnCard
        key={column.key}
        column={column}
        label={getColumnLabel(column.key)}
        cards={cardsByKey[column.key] ?? []}
        cardFields={cardFields}
        canWrite={canWrite}
        dragState={visualDragState}
        moveState={keyboard.moveState}
        onToggleCollapse={onToggleCollapse}
        onQuickCreate={onQuickCreate}
        highlightCardId={highlightCardId ?? null}
        onCardPointerDown={drag.onPointerDown}
        onCardKeyDown={keyboard.handleCardKeyDown}
        onSelectCard={selectCard}
        selectedCardId={selection?.cardId ?? null}
        fullListMode={fullListMode}
        dropKey={dropKey}
        testKey={testKey}
        subGroupKey={subGroupKey}
        toneClass={columnToneClass(column.key, groupBy)}
        executionStatusByIssueId={executionStatusByIssueId}
      />
    );
  };

  const activeCompactIndex = columns.length === 0 ? 0 : compactIndex % columns.length;

  return (
    <div
      className="mesh-board__columns-wrap"
      ref={boardRef}
      data-testid={
        subGroupKey === undefined ? 'board-columns-wrap' : `board-columns-wrap-${subGroupKey}`
      }
      data-a11y-list-mode={fullListMode ? 'full' : 'virtual'}
    >
      {/* aria-live 播报区(视觉隐藏,复用 design/base.css .sr-only):拖拽/键盘移动各阶段,§10.2。 */}
      <div className="sr-only" aria-live="assertive" data-testid="board-live">
        {announcement}
      </div>
      {columns.some((column) => shouldVirtualize((cardsByKey[column.key] ?? []).length)) ? (
        <Button
          variant="secondary"
          size="sm"
          className="mesh-board__a11y-list-toggle"
          aria-pressed={fullListMode}
          onClick={() => setFullListMode((current) => !current)}
        >
          {t(fullListMode ? 'board.useVirtualList' : 'board.useCompleteA11yList')}
        </Button>
      ) : null}
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
          computePosition={(groupKey, index) => computePosition(currentDropKey(groupKey), index)}
          onDropCard={dropCard}
          onClose={() => setTouchCardId(null)}
          announce={announce}
          getColumnLabel={getColumnLabel}
        />
      ) : null}
    </div>
  );
}
