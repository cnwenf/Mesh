/**
 * 看板「列表」布局的单元格子组件(标题/优先级/状态/负责人/更新时间 + 行操作菜单)。
 *
 * 拆分动机:BoardListView 主文件负责分组/排序/列选取/批量/选择等编排,
 * 单元格内联编辑(乐观更新 + 失败保留输入 + role=alert)逻辑独立于此,
 * 两者经 CellEditController 契约解耦。本文件仅导出组件与类型(react-refresh 友好)。
 */
import { useRef, useState } from 'react';
import { Avatar, Badge, Icon, Menu } from '../../design';
import type { MenuItem } from '../../design';
import { useT } from '../../i18n';
import { PRIORITY_ORDER, STATE_CATEGORY_ORDER } from '../issues/types';
import type { UpdateIssueBody } from '../issues/types';
import { buildCellPatch, categoryKey, priorityLabelText, statusTone } from './listCellEdit';
import type { CellField } from './listCellEdit';
import type { BoardCard } from './projection';

/** 单元格内联编辑编排契约(由 BoardListView 注入)。 */
export interface CellEditController {
  readonly canWrite: boolean;
  readonly editingCell: string | null;
  readonly cellErrors: Readonly<Record<string, string>>;
  readonly savingCells: ReadonlySet<string>;
  /** 状态分类 → status_id 映射(缺失的分类菜单项禁用)。 */
  readonly columnTargetStatus: Readonly<Record<string, string>>;
  readonly startEdit: (cellKey: string) => void;
  readonly cancelEdit: () => void;
  readonly commit: (
    card: BoardCard,
    field: CellField,
    patch: UpdateIssueBody,
    override: Partial<BoardCard>,
  ) => Promise<void>;
}

interface TitleCellProps {
  readonly card: BoardCard;
  readonly controller: CellEditController;
  readonly onOpenIssue: (issueId: string) => void;
}

/** 标题单元格:canWrite 点击进入内联编辑;只读时点击打开 issue。 */
export function TitleCell(props: TitleCellProps): React.JSX.Element {
  const { card, controller, onOpenIssue } = props;
  const { canWrite, editingCell, cellErrors, startEdit } = controller;
  const cellKey = `${card.id}:title`;

  if (editingCell === cellKey) {
    return <TitleInput card={card} cellKey={cellKey} controller={controller} />;
  }

  const handleClick = (): void => {
    if (canWrite) startEdit(cellKey);
    else onOpenIssue(card.id);
  };
  return (
    <span className="mesh-board-list__title-wrap">
      <button
        type="button"
        className="mesh-board-list__title-link mesh-text-body"
        data-testid={`list-title-${card.id}`}
        onClick={handleClick}
      >
        {card.title}
      </button>
      {cellErrors[cellKey] !== undefined ? (
        <span className="mesh-board-list__cell-error mesh-text-caption" role="alert">
          {cellErrors[cellKey]}
        </span>
      ) : null}
    </span>
  );
}

interface TitleInputProps {
  readonly card: BoardCard;
  readonly cellKey: string;
  readonly controller: CellEditController;
}

/** 标题内联输入:Enter/blur 保存,Esc 取消;失败保留已输入值并显示 role=alert。 */
function TitleInput(props: TitleInputProps): React.JSX.Element {
  const { card, controller } = props;
  const { cellErrors, savingCells, cancelEdit, commit } = controller;
  const t = useT();
  const [draft, setDraft] = useState(card.title);
  // 防止 Enter 与紧随其后的 blur 触发两次提交。
  const inFlightRef = useRef(false);
  const cellKey = `${card.id}:title`;

  const save = (): void => {
    if (inFlightRef.current) return;
    const value = draft.trim();
    if (value === card.title) {
      cancelEdit();
      return;
    }
    inFlightRef.current = true;
    void commit(card, 'title', buildCellPatch('title', value), { title: value }).finally(() => {
      inFlightRef.current = false;
    });
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === 'Enter') {
      event.preventDefault();
      save();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancelEdit();
    }
  };

  return (
    <span className="mesh-board-list__title-edit">
      <input
        className="mesh-board-list__title-input mesh-text-body"
        data-testid={`list-title-input-${card.id}`}
        aria-label={t('board.list.editTitle')}
        value={draft}
        autoFocus
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={save}
      />
      {savingCells.has(cellKey) ? (
        <Icon name="refresh" size={16} label={t('board.list.saving')} />
      ) : null}
      {cellErrors[cellKey] !== undefined ? (
        <span className="mesh-board-list__cell-error mesh-text-caption" role="alert">
          {cellErrors[cellKey]}
        </span>
      ) : null}
    </span>
  );
}

/** 只读优先级呈现(旗标 + 文案),供单元格只读分支与移动卡片复用。 */
export function PriorityText(props: { readonly card: BoardCard }): React.JSX.Element {
  const { card } = props;
  const t = useT();
  return (
    <span className="mesh-board-list__priority mesh-text-body">
      <Icon name="flag" size={16} />
      <span>{priorityLabelText(t, card.priority)}</span>
    </span>
  );
}

/** 优先级单元格:canWrite 时经 Menu 选择五级优先级并 PATCH。 */
export function PriorityCell(props: {
  readonly card: BoardCard;
  readonly controller: CellEditController;
}): React.JSX.Element {
  const { card, controller } = props;
  const { canWrite, cellErrors, savingCells, commit } = controller;
  const t = useT();
  const cellKey = `${card.id}:priority`;

  if (!canWrite) return <PriorityText card={card} />;

  const entries: MenuItem[] = PRIORITY_ORDER.map((priority) => ({
    key: `priority-${priority}`,
    label: priorityLabelText(t, priority),
    icon: 'flag',
    onSelect: () => {
      void commit(card, 'priority', buildCellPatch('priority', priority), { priority });
    },
  }));

  return (
    <span className="mesh-board-list__priority">
      <Menu
        trigger={<PriorityText card={card} />}
        triggerLabel={t('board.list.editPriority')}
        entries={entries}
      />
      {savingCells.has(cellKey) ? (
        <Icon name="refresh" size={16} label={t('board.list.saving')} />
      ) : null}
      {cellErrors[cellKey] !== undefined ? (
        <span className="mesh-board-list__cell-error mesh-text-caption" role="alert">
          {cellErrors[cellKey]}
        </span>
      ) : null}
    </span>
  );
}

/** 只读状态徽标(分类 tone + 状态名),供单元格只读分支与移动卡片复用。 */
export function StatusBadge(props: { readonly card: BoardCard }): React.JSX.Element {
  const { card } = props;
  const t = useT();
  const name = card.status?.name ?? t(`board.category.${categoryKey(card.state_category)}`);
  return (
    <Badge tone={statusTone(card.state_category)} size="sm">
      {name}
    </Badge>
  );
}

/** 状态单元格:canWrite 时经 Menu 选择七个状态分类(无映射者禁用)并 PATCH status_id。 */
export function StatusCell(props: {
  readonly card: BoardCard;
  readonly controller: CellEditController;
}): React.JSX.Element {
  const { card, controller } = props;
  const { canWrite, cellErrors, savingCells, columnTargetStatus, commit } = controller;
  const t = useT();
  const cellKey = `${card.id}:status`;

  if (!canWrite) return <StatusBadge card={card} />;

  const entries: MenuItem[] = STATE_CATEGORY_ORDER.map((category) => {
    const statusId = columnTargetStatus[category];
    return {
      key: `status-${category}`,
      label: t(`board.category.${category}`),
      disabled: statusId === undefined,
      onSelect: () => {
        if (statusId === undefined) return;
        void commit(card, 'status', buildCellPatch('status', statusId), {
          status_id: statusId,
          state_category: category,
          status: { id: statusId, name: t(`board.category.${category}`), category },
        });
      },
    };
  });

  return (
    <span className="mesh-board-list__status">
      <Menu
        trigger={<StatusBadge card={card} />}
        triggerLabel={t('board.list.editStatus')}
        entries={entries}
      />
      {savingCells.has(cellKey) ? (
        <Icon name="refresh" size={16} label={t('board.list.saving')} />
      ) : null}
      {cellErrors[cellKey] !== undefined ? (
        <span className="mesh-board-list__cell-error mesh-text-caption" role="alert">
          {cellErrors[cellKey]}
        </span>
      ) : null}
    </span>
  );
}

/** 负责人单元格:头像 + 名称;无人认领显示占位文案。 */
export function AssigneeCell(props: { readonly card: BoardCard }): React.JSX.Element {
  const { card } = props;
  const t = useT();
  if (card.assignee === null) {
    return (
      <span className="mesh-board-list__assignee-empty mesh-text-body">
        {t('board.list.unassigned')}
      </span>
    );
  }
  return (
    <span className="mesh-board-list__assignee mesh-text-body">
      <Avatar name={card.assignee.name} size={20} kind="human" />
      <span>{card.assignee.name}</span>
    </span>
  );
}

/** 更新时间单元格(本地化短日期时间;解析失败回退原始串)。 */
export function UpdatedCell(props: { readonly card: BoardCard }): React.JSX.Element {
  const { card } = props;
  const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'short' });
  const parsed = Date.parse(card.updated_at);
  const text = Number.isNaN(parsed) ? card.updated_at : formatter.format(new Date(parsed));
  return <span className="mesh-board-list__updated mesh-text-caption">{text}</span>;
}

interface RowActionsMenuProps {
  readonly card: BoardCard;
  readonly onOpenIssue: (issueId: string) => void;
  readonly onRequestDelete: (issueIds: readonly string[]) => void;
  /** L222:该 issue 是否已收藏(与 onToggleFavorite 同时提供时渲染星标条目)。 */
  readonly isFavorite?: boolean;
  readonly onToggleFavorite?: (issueId: string) => void;
}

/** 行操作菜单(打开 / 收藏? / 删除);hover/focus-within 显现,触屏恒显(CSS 控制)。 */
export function RowActionsMenu(props: RowActionsMenuProps): React.JSX.Element {
  const { card, onOpenIssue, onRequestDelete, isFavorite = false, onToggleFavorite } = props;
  const t = useT();
  const favoriteEntry: MenuItem | null =
    onToggleFavorite === undefined
      ? null
      : {
          key: 'favorite',
          label: isFavorite ? t('favorites.remove') : t('favorites.add'),
          icon: 'star',
          onSelect: () => onToggleFavorite(card.id),
        };
  const entries: MenuItem[] = [
    {
      key: 'open',
      label: t('board.list.open'),
      icon: 'external',
      onSelect: () => onOpenIssue(card.id),
    },
    ...(favoriteEntry === null ? [] : [favoriteEntry]),
    {
      key: 'delete',
      label: t('board.list.delete'),
      icon: 'trash',
      danger: true,
      onSelect: () => onRequestDelete([card.id]),
    },
  ];
  return (
    <div className="mesh-board-list__row-actions">
      <Menu
        trigger={<Icon name="more-horizontal" size={16} />}
        triggerLabel={t('board.list.rowActions')}
        entries={entries}
        align="end"
      />
    </div>
  );
}
