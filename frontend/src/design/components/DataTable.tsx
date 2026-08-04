/**
 * DataTable(design-quality §7.6/§10.2):语义化数据表格底座。
 *
 * - <table> + caption(可 sr-only)+ scope=col 表头 + 可选排序(aria-sort +
 *   表头按钮,方向经回调上抛,排序逻辑归调用方);
 * - 行高 default 44px / comfortable 52px;表头 12–13px,行内容 13–14px;
 * - 行主操作经 onRowClick(整行可点)或单元格内链接;次要操作由调用方放入
 *   hover/focus-within 显现的单元格;
 * - 空数据渲染 emptyState(常为 EmptyState 四部件,§7.7);
 * - 手机横向滚动由调用方包裹受控滚动容器(边缘提示 + 首列粘住,§7.6),
 *   本组件只保证表格自身不破版。无硬编码文案。
 */
import type { ComponentProps, ReactNode } from 'react';
import {
  Table as AppicaTable,
  TableBody as AppicaTableBody,
  TableCaption as AppicaTableCaption,
  TableCell as AppicaTableCell,
  TableHead as AppicaTableHead,
  TableHeader as AppicaTableHeader,
  TableRow as AppicaTableRow,
} from '@appica/ui-react/table';
import { Icon } from './Icon';
import './components.css';

export interface DataTableColumn<T> {
  /** 列唯一 id(排序键) */
  readonly id: string;
  /** 表头文案 */
  readonly header: string;
  /** 单元格渲染 */
  readonly cell: (row: T) => ReactNode;
  /** 可排序(表头渲染按钮 + aria-sort) */
  readonly sortable?: boolean;
  /** 内容对齐(数字/时间列用 end,启用 tabular-nums) */
  readonly align?: 'start' | 'end';
}

export interface DataTableSortState {
  readonly id: string;
  readonly direction: 'asc' | 'desc';
}

export interface DataTableProps<T> {
  /** 表格可访问标题(必填;hideCaption 时视觉隐藏但读屏可达) */
  caption: string;
  /** 视觉隐藏 caption(页面已有可见 h1 标题时) */
  hideCaption?: boolean;
  columns: ReadonlyArray<DataTableColumn<T>>;
  rows: ReadonlyArray<T>;
  rowKey: (row: T) => string;
  /** 排序状态(受控,由调用方持有) */
  sortBy?: DataTableSortState;
  /** 点击可排序表头:上抛列 id,方向切换逻辑归调用方 */
  onSortChange?: (columnId: string) => void;
  /** 行密度:default 44px / comfortable 52px */
  density?: 'default' | 'comfortable';
  /** 整行主操作回调(行渲染为可聚焦可点击) */
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined;
  /** 空数据插槽(§7.7 四部件空态) */
  emptyState?: ReactNode;
}

export type DataTableSurfaceProps = ComponentProps<typeof AppicaTable>;

/**
 * Appica-backed table root for page-specific compositions that need custom
 * headers, row keyboard state, or interactive cells beyond the column schema.
 */
export function DataTableSurface({
  size = 'sm',
  ...props
}: DataTableSurfaceProps): React.JSX.Element {
  return <AppicaTable size={size} {...props} />;
}

export function DataTable<T>(props: DataTableProps<T>): React.JSX.Element {
  const {
    caption,
    hideCaption = false,
    columns,
    rows,
    rowKey,
    sortBy,
    onSortChange,
    density = 'default',
    onRowClick,
    rowClassName,
    emptyState,
  } = props;

  const tableClasses = [
    'mesh-data-table',
    density === 'comfortable' ? 'mesh-data-table--comfortable' : null,
  ]
    .filter((part): part is string => Boolean(part))
    .join(' ');

  return (
    <DataTableSurface
      size={density === 'comfortable' ? 'md' : 'sm'}
      hoverableRows={onRowClick !== undefined}
      className={tableClasses}
    >
      <AppicaTableCaption
        className={
          hideCaption ? 'mesh-data-table__caption mesh-visually-hidden' : 'mesh-data-table__caption'
        }
      >
        {caption}
      </AppicaTableCaption>
      <AppicaTableHeader>
        <AppicaTableRow>
          {columns.map((column) => {
            const isSorted = sortBy?.id === column.id;
            const ariaSort = isSorted
              ? sortBy.direction === 'asc'
                ? 'ascending'
                : 'descending'
              : undefined;
            const alignClass = column.align === 'end' ? ' mesh-data-table__cell--end' : '';
            if (column.sortable === true) {
              return (
                <AppicaTableHead
                  key={column.id}
                  scope="col"
                  aria-sort={ariaSort}
                  className={`mesh-data-table__header${alignClass}`}
                >
                  <button
                    type="button"
                    className="mesh-data-table__sort"
                    onClick={() => onSortChange?.(column.id)}
                  >
                    {column.header}
                    {isSorted ? (
                      <Icon
                        name={sortBy.direction === 'asc' ? 'chevron-up' : 'chevron-down'}
                        size={16}
                        className="mesh-data-table__sort-indicator"
                      />
                    ) : null}
                  </button>
                </AppicaTableHead>
              );
            }
            return (
              <AppicaTableHead key={column.id} scope="col" className={`mesh-data-table__header${alignClass}`}>
                {column.header}
              </AppicaTableHead>
            );
          })}
        </AppicaTableRow>
      </AppicaTableHeader>
      <AppicaTableBody>
        {rows.length === 0 && emptyState !== undefined ? (
          <AppicaTableRow>
            <AppicaTableCell colSpan={columns.length} className="mesh-data-table__empty">
              {emptyState}
            </AppicaTableCell>
          </AppicaTableRow>
        ) : (
          rows.map((row) => {
            const extra = rowClassName?.(row);
            const rowClasses = ['mesh-data-table__row', extra]
              .filter((p): p is string => Boolean(p))
              .join(' ');
            return (
              <AppicaTableRow
                key={rowKey(row)}
                className={rowClasses}
                onClick={
                  onRowClick !== undefined
                    ? (event) => {
                        // 守卫(验收 R1-M4):行内交互元素(链接/按钮/表单控件)的点击
                        // 自行负责,不冒泡为整行导航(与 onKeyDown 的 target 守卫同构)。
                        const target = event.target;
                        if (
                          target instanceof HTMLElement &&
                          target.closest(
                            'a, button, [role="button"], input, select, textarea, label',
                          ) !== null
                        ) {
                          return;
                        }
                        onRowClick(row);
                      }
                    : undefined
                }
                tabIndex={onRowClick !== undefined ? 0 : undefined}
                onKeyDown={
                  onRowClick !== undefined
                    ? (event) => {
                        if (event.key === 'Enter' && event.target === event.currentTarget) {
                          event.preventDefault();
                          onRowClick(row);
                        }
                      }
                    : undefined
                }
              >
                {columns.map((column) => (
                  <AppicaTableCell
                    key={column.id}
                    className={
                      column.align === 'end'
                        ? 'mesh-data-table__cell mesh-data-table__cell--end'
                        : 'mesh-data-table__cell'
                    }
                  >
                    {column.cell(row)}
                  </AppicaTableCell>
                ))}
              </AppicaTableRow>
            );
          })
        )}
      </AppicaTableBody>
    </DataTableSurface>
  );
}
