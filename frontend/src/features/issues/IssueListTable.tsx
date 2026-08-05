/**
 * Issue 列表表格(design-quality.md §7.6 / §3.2):
 * 语义 <table>(caption/列头/aria-sort)+ 可排序表头(点击循环 升/降/无,客户端排序)+
 * 行主操作 = 标题链接;次要操作仅在行 Menu(hover/focus-within 显示,触控常驻)+
 * 键盘游标选择(roving tabindex,↑↓移动 / Enter 打开 / 空格切换,§10.2)。
 * 手机(≤599px)经 CSS 将同一表格重排为主次行卡片(编号+标题为主行,
 * 状态/优先级/负责人/截止日为次行徽章),不复制 DOM(读屏/焦点序单一,§7.6/§11.2)。
 */
/* eslint-disable react-refresh/only-export-components -- formatDueDate 为行内纯助手,与表格组件同模块契约 */
import { Link } from 'react-router';
import { useIntl } from 'react-intl';
import { Avatar, Badge, Button, Checkbox, DataTableSurface, Icon, Menu } from '../../design';
import type { ListKeyboardSelection } from '../../design';
import { formatDate, useT } from '../../i18n';
import { LabelDots } from '../labels/LabelDots';
import type { IssueSummary } from './types';
import type { IssueSortField, IssueSortState } from './issuesSort';
import { categoryTone } from './issuePresentation';
import { workspaceIssueByIdentifierPath } from './issueRoutes';
import './issues.css';

/**
 * 截止日本地化(due_date 为纯日期值,UTC 解析锁定,避免展示时区换算漂移日历日;
 * 非法值降级回显原值,单条坏数据不中断整页,LOW-2 / i18n.md §4.4)。
 */
export function formatDueDate(dueDate: string | null, locale: string): string {
  if (dueDate === null) return '';
  try {
    return formatDate(dueDate, { locale, timeZone: 'UTC', dateStyle: 'medium' });
  } catch {
    return dueDate;
  }
}

interface SortableHeaderProps {
  readonly field: IssueSortField;
  readonly label: string;
  readonly sort: IssueSortState | null;
  readonly onSort: (field: IssueSortField) => void;
}

/** 可排序列表头:aria-sort 三态 + 方向图标(非颜色唯一信号,§7.6)。 */
function SortableHeader(props: SortableHeaderProps): React.JSX.Element {
  const { field, label, sort, onSort } = props;
  const active = sort !== null && sort.field === field;
  const ariaSort = active ? (sort.order === 'asc' ? 'ascending' : 'descending') : 'none';
  return (
    <th scope="col" aria-sort={ariaSort} className="mesh-issues__th--sortable">
      <Button
        variant="ghost"
        size="sm"
        className="mesh-issues__sort-button"
        onClick={() => onSort(field)}
      >
        {label}
        {active ? <Icon name={sort.order === 'asc' ? 'arrow-up' : 'arrow-down'} size={16} /> : null}
      </Button>
    </th>
  );
}

interface IssueRowProps {
  readonly workspaceSlug: string;
  readonly issue: IssueSummary;
  readonly index: number;
  readonly isSelected: boolean;
  readonly onToggleOne: (id: string) => void;
  readonly onOpen: (issue: IssueSummary) => void;
  readonly keyboard: ListKeyboardSelection;
  readonly locale: string;
}

function IssueRow(props: IssueRowProps): React.JSX.Element {
  const { workspaceSlug, issue, index, isSelected, onToggleOne, onOpen, keyboard, locale } = props;
  const t = useT();
  const assigneeName =
    issue.assignee !== null
      ? `${issue.assignee.name}${issue.assignee.member_type === 'agent' ? ` (${t('issues.agentBadge')})` : ''}`
      : t('issues.unassigned');
  return (
    <tr
      className={isSelected ? 'mesh-issues__row mesh-issues__row--selected' : 'mesh-issues__row'}
      data-testid={`issue-row-${issue.identifier}`}
      data-list-item-index={index}
      tabIndex={keyboard.itemTabIndex(index)}
      onKeyDown={(event) => keyboard.handleItemKeyDown(event, index)}
      onFocus={() => keyboard.activate(index)}
    >
      <td className="mesh-issues__cell--select">
        <Checkbox
          className="mesh-issues__selection-control"
          label={`${t('issues.columns.select')} ${issue.identifier}`}
          checked={isSelected}
          onChange={() => onToggleOne(issue.id)}
          aria-label={`${t('issues.columns.select')} ${issue.identifier}`}
          data-testid={`issue-select-${issue.id}`}
        />
      </td>
      <td className="mesh-issues__identifier">{issue.identifier}</td>
      <td className="mesh-issues__cell--title">
        <Link
          to={workspaceIssueByIdentifierPath(workspaceSlug, issue.identifier)}
          className="mesh-issues__title-link"
        >
          {issue.title}
        </Link>
      </td>
      <td className="mesh-issues__cell--status">
        <span
          className="mesh-issues__status-accent"
          data-testid={`issue-status-${issue.id}`}
          style={
            issue.status !== null && issue.status.color !== null
              ? { borderColor: issue.status.color }
              : undefined
          }
        >
          <Badge tone={categoryTone(issue.state_category)} className="mesh-issues__status-chip">
            {issue.status !== null
              ? issue.status.name
              : t(`issues.category.${issue.state_category}`)}
          </Badge>
        </span>
      </td>
      <td className="mesh-issues__cell--labels">
        <LabelDots labels={issue.labels ?? []} />
      </td>
      <td className="mesh-issues__cell--priority">{t(`issues.priority.${issue.priority}`)}</td>
      <td className="mesh-issues__cell--assignee">
        {issue.assignee !== null ? (
          <Avatar name={issue.assignee.name} kind={issue.assignee.member_type} size={20} />
        ) : null}
        <span>{assigneeName}</span>
      </td>
      <td className="mesh-issues__cell--due">{formatDueDate(issue.due_date, locale)}</td>
      <td className="mesh-issues__cell--actions">
        <Menu
          align="end"
          triggerLabel={t('issues.rowActions')}
          trigger={<Icon name="more-horizontal" size={16} />}
          entries={[
            {
              key: 'open',
              label: t('issues.rowOpen'),
              icon: 'external',
              onSelect: () => onOpen(issue),
            },
            {
              key: 'toggle',
              label: isSelected ? t('issues.rowDeselect') : t('issues.rowSelect'),
              icon: 'check',
              onSelect: () => onToggleOne(issue.id),
            },
          ]}
        />
      </td>
    </tr>
  );
}

export interface IssueListTableProps {
  readonly workspaceSlug: string;
  readonly issues: readonly IssueSummary[];
  readonly sort: IssueSortState | null;
  readonly onSort: (field: IssueSortField) => void;
  readonly selected: ReadonlySet<string>;
  readonly onToggleOne: (id: string) => void;
  readonly onToggleAll: () => void;
  readonly allSelected: boolean;
  readonly someSelected: boolean;
  readonly keyboard: ListKeyboardSelection;
  readonly onOpen: (issue: IssueSummary) => void;
}

export function IssueListTable(props: IssueListTableProps): React.JSX.Element {
  const {
    workspaceSlug,
    issues,
    sort,
    onSort,
    selected,
    onToggleOne,
    onToggleAll,
    allSelected,
    someSelected,
    keyboard,
    onOpen,
  } = props;
  const t = useT();
  const intl = useIntl();
  return (
    <div className="mesh-issues__list-container" ref={keyboard.containerRef}>
      <DataTableSurface className="mesh-issues__table" data-testid="issue-table">
        <caption className="sr-only">{t('issues.tableCaption')}</caption>
        <thead>
          <tr>
            <th scope="col" className="mesh-issues__th--select">
              <Checkbox
                className="mesh-issues__selection-control"
                label={t('issues.columns.selectAll')}
                checked={allSelected}
                indeterminate={someSelected && !allSelected}
                onChange={onToggleAll}
                aria-label={t('issues.columns.selectAll')}
                data-testid="issue-select-all"
              />
            </th>
            <SortableHeader
              field="identifier"
              label={t('issues.columns.key')}
              sort={sort}
              onSort={onSort}
            />
            <SortableHeader
              field="title"
              label={t('issues.columns.title')}
              sort={sort}
              onSort={onSort}
            />
            <th scope="col">{t('issues.columns.status')}</th>
            <th scope="col">{t('issues.columns.labels')}</th>
            <SortableHeader
              field="priority"
              label={t('issues.columns.priority')}
              sort={sort}
              onSort={onSort}
            />
            <th scope="col">{t('issues.columns.assignee')}</th>
            <SortableHeader
              field="due"
              label={t('issues.columns.due')}
              sort={sort}
              onSort={onSort}
            />
            <th scope="col" className="mesh-issues__th--actions">
              <span className="sr-only">{t('issues.rowActions')}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {issues.map((issue, index) => (
            <IssueRow
              key={issue.id}
              workspaceSlug={workspaceSlug}
              issue={issue}
              index={index}
              isSelected={selected.has(issue.id)}
              onToggleOne={onToggleOne}
              onOpen={onOpen}
              keyboard={keyboard}
              locale={intl.locale}
            />
          ))}
        </tbody>
      </DataTableSurface>
    </div>
  );
}
