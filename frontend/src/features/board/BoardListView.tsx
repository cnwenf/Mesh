/**
 * 看板「列表」布局(BoardPage 经 layout 切换装载)。
 *
 * 结构:
 * - 分组以「可折叠分组行」呈现;单一 <table> 承载全部行(每组一个 <tbody>),
 *   另渲染一份移动卡片清单,两者由 CSS display 依窗宽切换(jsdom 同时渲染)。
 * - 表头可排序(纯比较器见 listCellEdit.ts);列选取仅本地态(不持久化)。
 * - 单元格内联编辑与乐观更新经 boardListCells 的 CellEditController 编排。
 * - 多选 + 批量(状态/优先级/删除)走 issues/api.bulkIssues。
 *
 * 规模化:≥200 行时当前平铺渲染,但所有行经单一 RowRenderer 漏斗,
 * 以便日后以虚拟化(useVirtualWindow,平行开发中)包裹而不改行渲染契约。
 */
import { useCallback, useMemo, useState } from 'react';
import { BulkBar, Button, Dialog, Icon, Menu, useToast } from '../../design';
import type { MenuItem, ToastContextValue } from '../../design';
import { useT } from '../../i18n';
import type { TranslateFn } from '../../i18n';
import { MeshApiError, errorToI18nKey } from '../../api';
import { getApiClient } from '../../api/instance';
import { bulkIssues, updateIssue } from '../issues/api';
import { PRIORITY_ORDER, STATE_CATEGORY_ORDER } from '../issues/types';
import type { BulkBody, IssuePriority, UpdateIssueBody } from '../issues/types';
import {
  AssigneeCell,
  PriorityCell,
  RowActionsMenu,
  StatusBadge,
  PriorityText,
  StatusCell,
  TitleCell,
  UpdatedCell,
} from './boardListCells';
import type { CellEditController } from './boardListCells';
import {
  ALL_COLUMN_IDS,
  compareCards,
  groupLabelText,
  nextSortDir,
  resolveVisibleColumns,
} from './listCellEdit';
import type { CellField, ColumnId, SortDir, SortField } from './listCellEdit';
import type { BoardCard, BoardGroup } from './projection';
import type { View } from './types';
import './board-list.css';

export interface BoardListViewProps {
  readonly view: View;
  readonly groups: readonly BoardGroup[];
  readonly columnTargetStatus: Readonly<Record<string, string>>;
  readonly canWrite: boolean;
  readonly onOpenIssue: (issueId: string) => void;
  readonly onChanged: () => void;
}

/** 列 id → 表头 i18n 键。 */
const COLUMN_LABEL_KEYS: Readonly<Record<ColumnId, string>> = Object.freeze({
  identifier: 'board.list.identifier',
  title: 'board.list.title',
  status: 'board.list.status',
  priority: 'board.list.priority',
  assignee: 'board.list.assignee',
  updated: 'board.list.updated',
});

/** board_settings 可能内嵌 display_fields(视图配置增量),类型层未声明,此处局部收窄。 */
function settingsDisplayFields(view: View): readonly string[] | undefined {
  return (view.board_settings as { display_fields?: readonly string[] }).display_fields;
}

/** 从错误对象提取批量部分失败明细(succeeded/failed)。 */
function readBulkPartialDetails(err: MeshApiError): { succeeded: number; failed: number } {
  const details = err.details ?? {};
  const succeeded = typeof details.succeeded === 'number' ? details.succeeded : 0;
  const failed = typeof details.failed === 'number' ? details.failed : 0;
  return { succeeded, failed };
}

/** 批量结果 toast:部分失败 → 「成功 N / 失败 M」(warn);其余错误 → 危险提示。 */
function reportBulkError(err: unknown, t: TranslateFn, toast: ToastContextValue): void {
  const closeLabel = t('common.close');
  if (err instanceof MeshApiError && err.code === 'bulk_partial_failure') {
    const { succeeded, failed } = readBulkPartialDetails(err);
    toast.addToast(t('board.list.result', { succeeded, failed }), { tone: 'warn', closeLabel });
    return;
  }
  const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
  toast.addToast(t(key), { tone: 'danger', closeLabel });
}

/** 从记录中移除键(不可变;键不存在返回原对象)。 */
function withoutKey(
  map: Readonly<Record<string, string>>,
  key: string,
): Readonly<Record<string, string>> {
  if (map[key] === undefined) return map;
  const next = { ...map };
  delete next[key];
  return next;
}

interface RowRendererProps {
  readonly card: BoardCard;
  readonly controller: CellEditController;
  readonly visibleColumns: readonly ColumnId[];
  readonly isSelected: boolean;
  readonly onToggleRow: (issueId: string) => void;
  readonly onOpenIssue: (issueId: string) => void;
  readonly onRequestDelete: (issueIds: readonly string[]) => void;
}

/** 单行渲染(表格 <tr>);所有行经此组件漏斗,便于日后虚拟化包裹。 */
function RowRenderer(props: RowRendererProps): React.JSX.Element {
  const { card, controller, visibleColumns, isSelected, onToggleRow, onOpenIssue, onRequestDelete } = props;
  const t = useT();
  return (
    <tr
      className={isSelected ? 'mesh-board-list__row mesh-board-list__row--selected' : 'mesh-board-list__row'}
      data-testid={`list-row-${card.id}`}
    >
      <td className="mesh-board-list__td mesh-board-list__td--select">
        <input
          type="checkbox"
          className="mesh-board-list__row-checkbox"
          data-testid={`list-select-${card.id}`}
          aria-label={t('board.list.selectRow', { identifier: card.identifier })}
          checked={isSelected}
          onChange={() => onToggleRow(card.id)}
        />
      </td>
      {visibleColumns.map((column) => (
        <td key={column} className="mesh-board-list__td">
          <CellValue
            column={column}
            card={card}
            controller={controller}
            onOpenIssue={onOpenIssue}
          />
        </td>
      ))}
      <td className="mesh-board-list__td mesh-board-list__td--actions">
        <RowActionsMenu card={card} onOpenIssue={onOpenIssue} onRequestDelete={onRequestDelete} />
      </td>
    </tr>
  );
}

interface CellValueProps {
  readonly column: ColumnId;
  readonly card: BoardCard;
  readonly controller: CellEditController;
  readonly onOpenIssue: (issueId: string) => void;
}

/** 依列 id 分派单元格内容。 */
function CellValue(props: CellValueProps): React.JSX.Element {
  const { column, card, controller, onOpenIssue } = props;
  switch (column) {
    case 'identifier':
      return <span className="mesh-board-list__identifier mesh-text-caption">{card.identifier}</span>;
    case 'title':
      return <TitleCell card={card} controller={controller} onOpenIssue={onOpenIssue} />;
    case 'status':
      return <StatusCell card={card} controller={controller} />;
    case 'priority':
      return <PriorityCell card={card} controller={controller} />;
    case 'assignee':
      return <AssigneeCell card={card} />;
    case 'updated':
      return <UpdatedCell card={card} />;
  }
}

interface MobileRowProps {
  readonly card: BoardCard;
  readonly isSelected: boolean;
  readonly onToggleRow: (issueId: string) => void;
  readonly onOpenIssue: (issueId: string) => void;
  readonly onRequestDelete: (issueIds: readonly string[]) => void;
}

/** 移动端堆叠卡片(≤599px 显现):主行编号+标题,副行状态/优先级/负责人/时间。 */
function MobileRow(props: MobileRowProps): React.JSX.Element {
  const { card, isSelected, onToggleRow, onOpenIssue, onRequestDelete } = props;
  const t = useT();
  return (
    <li className="mesh-board-list__card" data-testid={`list-card-${card.id}`}>
      <div className="mesh-board-list__card-primary">
        <input
          type="checkbox"
          className="mesh-board-list__row-checkbox"
          aria-label={t('board.list.selectRow', { identifier: card.identifier })}
          checked={isSelected}
          onChange={() => onToggleRow(card.id)}
        />
        <span className="mesh-board-list__identifier mesh-text-caption">{card.identifier}</span>
        <button
          type="button"
          className="mesh-board-list__title-link mesh-text-body"
          onClick={() => onOpenIssue(card.id)}
        >
          {card.title}
        </button>
      </div>
      <div className="mesh-board-list__card-secondary">
        <StatusBadge card={card} />
        <PriorityText card={card} />
        <AssigneeCell card={card} />
        <UpdatedCell card={card} />
        <RowActionsMenu card={card} onOpenIssue={onOpenIssue} onRequestDelete={onRequestDelete} />
      </div>
    </li>
  );
}

interface GroupSectionProps {
  readonly group: BoardGroup;
  readonly cards: readonly BoardCard[];
  readonly label: string;
  readonly isCollapsed: boolean;
  readonly controller: CellEditController;
  readonly visibleColumns: readonly ColumnId[];
  readonly selected: ReadonlySet<string>;
  readonly onToggleGroup: (key: string) => void;
  readonly onToggleRow: (issueId: string) => void;
  readonly onOpenIssue: (issueId: string) => void;
  readonly onRequestDelete: (issueIds: readonly string[]) => void;
}

/** 分组区:桌面为 <tbody>(分组头行 + 数据行),移动为堆叠卡片清单。 */
function GroupSection(props: GroupSectionProps): React.JSX.Element {
  const {
    group, cards, label, isCollapsed, controller, visibleColumns, selected,
    onToggleGroup, onToggleRow, onOpenIssue, onRequestDelete,
  } = props;
  const t = useT();
  const colSpan = visibleColumns.length + 2;
  const chevron = isCollapsed ? 'chevron-right' : 'chevron-down';
  const toggleLabel = isCollapsed ? t('board.list.expandGroup') : t('board.list.collapseGroup');

  return (
    <tbody className="mesh-board-list__group" data-testid={`list-group-${group.key}`}>
      <tr className="mesh-board-list__group-row">
        <td colSpan={colSpan} className="mesh-board-list__group-cell">
          <button
            type="button"
            className="mesh-board-list__group-toggle mesh-text-body"
            data-testid={`list-group-toggle-${group.key}`}
            aria-expanded={!isCollapsed}
            aria-label={`${toggleLabel} ${label}`}
            onClick={() => onToggleGroup(group.key)}
          >
            <Icon name={chevron} size={16} />
            <span className="mesh-board-list__group-label">{label}</span>
            <span className="mesh-board-list__group-count mesh-text-caption">{group.count}</span>
          </button>
        </td>
      </tr>
      {isCollapsed
        ? null
        : cards.map((card) => (
            <RowRenderer
              key={card.id}
              card={card}
              controller={controller}
              visibleColumns={visibleColumns}
              isSelected={selected.has(card.id)}
              onToggleRow={onToggleRow}
              onOpenIssue={onOpenIssue}
              onRequestDelete={onRequestDelete}
            />
          ))}
    </tbody>
  );
}

export function BoardListView(props: BoardListViewProps): React.JSX.Element {
  const { view, groups, columnTargetStatus, canWrite, onOpenIssue, onChanged } = props;
  const t = useT();
  const toast = useToast();

  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [sortField, setSortField] = useState<SortField | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('none');
  // 列选取仅本地态(不写回视图配置):选取/重排经 display_fields 解析初始化后,
  // 由列选取菜单本地切换,刷新即重置(持久化属视图保存增量)。
  const [visibleColumns, setVisibleColumns] = useState<readonly ColumnId[]>(() =>
    resolveVisibleColumns(view.display_fields, settingsDisplayFields(view)),
  );
  const [editingCell, setEditingCell] = useState<string | null>(null);
  const [cellErrors, setCellErrors] = useState<Readonly<Record<string, string>>>({});
  const [savingCells, setSavingCells] = useState<ReadonlySet<string>>(new Set());
  // 乐观覆盖层:成功后按 card.id 叠加补丁字段(不改动 props,渲染时合并)。
  const [cardOverrides, setCardOverrides] = useState<Readonly<Record<string, Partial<BoardCard>>>>({});
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [confirmDeleteIds, setConfirmDeleteIds] = useState<readonly string[] | null>(null);

  const effectiveCard = useCallback(
    (card: BoardCard): BoardCard => {
      const override = cardOverrides[card.id];
      return override === undefined ? card : { ...card, ...override };
    },
    [cardOverrides],
  );

  const commit = useCallback(
    async (card: BoardCard, field: CellField, patch: UpdateIssueBody, override: Partial<BoardCard>) => {
      const cellKey = `${card.id}:${field}`;
      setSavingCells((prev) => new Set(prev).add(cellKey));
      try {
        await updateIssue(getApiClient(), card.id, { ...patch, version: card.version }, card.updated_at);
        setCardOverrides((prev) => ({ ...prev, [card.id]: { ...prev[card.id], ...override } }));
        setCellErrors((prev) => withoutKey(prev, cellKey));
        setEditingCell(null);
        onChanged();
        toast.addToast(t('board.list.savedToast'), { tone: 'success', closeLabel: t('common.close') });
      } catch (err) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'state.errorDescription';
        setCellErrors((prev) => ({ ...prev, [cellKey]: t(key) }));
      } finally {
        setSavingCells((prev) => {
          const next = new Set(prev);
          next.delete(cellKey);
          return next;
        });
      }
    },
    [onChanged, t, toast],
  );

  const controller: CellEditController = useMemo(
    () => ({
      canWrite,
      editingCell,
      cellErrors,
      savingCells,
      columnTargetStatus,
      startEdit: (key) => setEditingCell(key),
      cancelEdit: () => setEditingCell(null),
      commit,
    }),
    [canWrite, editingCell, cellErrors, savingCells, columnTargetStatus, commit],
  );

  const handleSort = (field: SortField): void => {
    if (sortField !== field) {
      setSortField(field);
      setSortDir('asc');
      return;
    }
    const next = nextSortDir(sortDir);
    if (next === 'none') {
      setSortField(null);
      setSortDir('none');
    } else {
      setSortDir(next);
    }
  };

  const ariaSortFor = (field: SortField): 'ascending' | 'descending' | 'none' => {
    if (sortField !== field) return 'none';
    if (sortDir === 'asc') return 'ascending';
    if (sortDir === 'desc') return 'descending';
    return 'none';
  };

  const sortGroupCards = useCallback(
    (cards: readonly BoardCard[]): readonly BoardCard[] => {
      const mapped = cards.map(effectiveCard);
      if (sortField === null || sortDir === 'none') return mapped;
      return [...mapped].sort(compareCards(sortField, sortDir));
    },
    [effectiveCard, sortField, sortDir],
  );

  const toggleGroup = (key: string): void => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleColumn = (column: ColumnId): void => {
    setVisibleColumns((current) => {
      const isVisible = current.includes(column);
      if (isVisible && current.length <= 1) return current; // 至少保留一列
      const present = new Set(current);
      if (isVisible) present.delete(column);
      else present.add(column);
      return ALL_COLUMN_IDS.filter((candidate) => present.has(candidate));
    });
  };

  const toggleRow = (issueId: string): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(issueId)) next.delete(issueId);
      else next.add(issueId);
      return next;
    });
  };

  // 全选作用于「已加载且展开分组」的行(折叠分组不可见,不参与)。
  const visibleIds = useMemo(
    () => groups.flatMap((group) => (collapsed.has(group.key) ? [] : group.data.map((card) => card.id))),
    [groups, collapsed],
  );
  const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const toggleAll = (): void => {
    setSelected(allSelected ? new Set<string>() : new Set(visibleIds));
  };

  const selectedIds = useMemo(() => [...selected], [selected]);

  const runBulk = useCallback(
    async (body: BulkBody) => {
      try {
        const result = await bulkIssues(getApiClient(), body);
        toast.addToast(t('board.list.result', { succeeded: result.succeeded, failed: result.failed }), {
          tone: result.failed > 0 ? 'warn' : 'success',
          closeLabel: t('common.close'),
        });
      } catch (err) {
        reportBulkError(err, t, toast);
      } finally {
        setSelected(new Set());
        onChanged();
      }
    },
    [onChanged, t, toast],
  );

  const bulkStatus = (category: string): void => {
    const statusId = columnTargetStatus[category];
    if (statusId === undefined) return;
    void runBulk({ issue_ids: selectedIds, changes: { status_id: statusId } });
  };

  const bulkPriority = (priority: string): void => {
    void runBulk({ issue_ids: selectedIds, changes: { priority: priority as IssuePriority } });
  };

  const confirmDelete = (): void => {
    if (confirmDeleteIds === null) return;
    const ids = confirmDeleteIds;
    setConfirmDeleteIds(null);
    void runBulk({ issue_ids: ids, delete: true });
  };

  if (groups.length === 0) {
    return (
      <div className="mesh-board-list" data-testid="list-empty">
        <p className="mesh-board-list__empty-title mesh-text-title-3">{t('board.list.emptyTitle')}</p>
        <p className="mesh-board-list__empty-description mesh-text-body">{t('board.list.emptyDescription')}</p>
      </div>
    );
  }

  const columnEntries: MenuItem[] = ALL_COLUMN_IDS.map((column) => ({
    key: `column-${column}`,
    label: t(COLUMN_LABEL_KEYS[column]),
    icon: visibleColumns.includes(column) ? 'check' : undefined,
    onSelect: () => toggleColumn(column),
  }));

  const bulkStatusEntries: MenuItem[] = STATE_CATEGORY_ORDER.map((category) => ({
    key: `bulk-status-${category}`,
    label: t(`board.category.${category}`),
    disabled: columnTargetStatus[category] === undefined,
    onSelect: () => bulkStatus(category),
  }));

  const bulkPriorityEntries: MenuItem[] = PRIORITY_ORDER.map((priority) => ({
    key: `bulk-priority-${priority}`,
    label: t(`board.priority.${priority}`),
    onSelect: () => bulkPriority(priority),
  }));

  return (
    <div className="mesh-board-list" data-testid="board-list-view">
      <div className="mesh-board-list__toolbar">
        <Menu
          trigger={<Icon name="list" size={16} />}
          triggerLabel={t('board.list.columns')}
          entries={columnEntries}
          align="end"
        />
      </div>

      <table className="mesh-board-list__table">
        <caption className="sr-only">{t('board.list.tableCaption')}</caption>
        <thead>
          <tr>
            <th scope="col" className="mesh-board-list__th mesh-board-list__th--select">
              <input
                type="checkbox"
                className="mesh-board-list__row-checkbox"
                data-testid="list-select-all"
                aria-label={t('board.list.selectAll')}
                checked={allSelected}
                onChange={toggleAll}
              />
            </th>
            {visibleColumns.map((column) => (
              <th
                key={column}
                scope="col"
                className="mesh-board-list__th"
                data-testid={`list-th-${column}`}
                aria-sort={ariaSortFor(column)}
              >
                <button
                  type="button"
                  className="mesh-board-list__sort-button mesh-text-caption"
                  data-testid={`list-sort-${column}`}
                  onClick={() => handleSort(column)}
                >
                  {t(COLUMN_LABEL_KEYS[column])}
                </button>
              </th>
            ))}
            <th scope="col" className="mesh-board-list__th mesh-board-list__th--actions">
              <span className="sr-only">{t('board.list.rowActions')}</span>
            </th>
          </tr>
        </thead>
        {groups.map((group) => (
          <GroupSection
            key={group.key}
            group={group}
            cards={sortGroupCards(group.data)}
            label={groupLabelText(view, group, t)}
            isCollapsed={collapsed.has(group.key)}
            controller={controller}
            visibleColumns={visibleColumns}
            selected={selected}
            onToggleGroup={toggleGroup}
            onToggleRow={toggleRow}
            onOpenIssue={onOpenIssue}
            onRequestDelete={setConfirmDeleteIds}
          />
        ))}
      </table>

      <div className="mesh-board-list__mobile">
        {groups.map((group) =>
          collapsed.has(group.key) ? null : (
            <ul key={group.key} className="mesh-board-list__cards" data-testid={`list-cards-${group.key}`}>
              {sortGroupCards(group.data).map((card) => (
                <MobileRow
                  key={card.id}
                  card={card}
                  isSelected={selected.has(card.id)}
                  onToggleRow={toggleRow}
                  onOpenIssue={onOpenIssue}
                  onRequestDelete={setConfirmDeleteIds}
                />
              ))}
            </ul>
          ),
        )}
      </div>

      <BulkBar
        selectedCount={selected.size}
        countLabel={t('board.list.selectedCount', { count: selected.size })}
        onClearSelection={() => setSelected(new Set())}
        clearLabel={t('board.list.clearSelection')}
        ariaLabel={t('board.list.bulkBarLabel')}
        actions={
          <div className="mesh-board-list__bulk-actions">
            <Menu
              trigger={<span>{t('board.list.bulkStatus')}</span>}
              triggerLabel={t('board.list.bulkStatus')}
              entries={bulkStatusEntries}
              align="end"
            />
            <Menu
              trigger={<span>{t('board.list.bulkPriority')}</span>}
              triggerLabel={t('board.list.bulkPriority')}
              entries={bulkPriorityEntries}
              align="end"
            />
            <Button variant="danger" size="sm" onClick={() => setConfirmDeleteIds(selectedIds)}>
              {t('board.list.bulkDelete')}
            </Button>
          </div>
        }
      />

      <Dialog
        open={confirmDeleteIds !== null}
        onClose={() => setConfirmDeleteIds(null)}
        title={t('board.list.deleteConfirmTitle')}
        closeLabel={t('common.close')}
      >
        <p className="mesh-text-body">
          {t('board.list.deleteConfirmBody', { count: confirmDeleteIds?.length ?? 0 })}
        </p>
        <div className="mesh-board-list__dialog-actions">
          <Button variant="secondary" size="md" onClick={() => setConfirmDeleteIds(null)}>
            {t('common.cancel')}
          </Button>
          <Button variant="danger" size="md" data-testid="list-delete-confirm" onClick={confirmDelete}>
            {t('board.list.deleteConfirm')}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
