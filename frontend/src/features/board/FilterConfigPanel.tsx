/**
 * 筛选配置面板(kanban.md §4.2 筛选器弹层):字段 + 操作符 + 值的组合条件,
 * 支持 AND/OR 与嵌套分组(≤3 层,≤20 条件,README §6.14 —— 结构校验在服务端
 * 权威执行,此处仅做 UI 编辑)。实时预览命中数属投影增量,本切片呈现占位「—」。
 */
/* eslint-disable react-refresh/only-export-components -- 草稿互转纯函数与面板组件同模块契约(测试直接引用) */
import { Button, Icon, Input, Select } from '../../design';
import { useT } from '../../i18n';
import type { FilterCondition, FilterGroup, Filters, FilterOp } from './types';

/** 内置可筛选字段(kanban §2.3)。 */
export const FILTER_FIELD_OPTIONS: readonly string[] = [
  'state_category',
  'status_id',
  'priority',
  'assignee_id',
  'reporter_id',
  'project_id',
  'cycle_id',
  'milestone_id',
  'due_date',
  'start_date',
  'created_at',
  'updated_at',
  'label',
  'parent_id',
  'q',
];

const OP_OPTIONS: readonly FilterOp[] = [
  'eq',
  'neq',
  'in',
  'not_in',
  'lt',
  'lte',
  'gt',
  'gte',
  'is_null',
  'is_not_null',
  'contains',
];

interface FieldConditionDraft {
  field: string;
  op: FilterOp;
  value: string;
}

interface GroupDraft {
  operator: 'AND' | 'OR';
  conditions: Array<FieldConditionDraft | GroupDraft>;
}

function isGroupDraft(draft: FieldConditionDraft | GroupDraft): draft is GroupDraft {
  return 'operator' in draft;
}

function isFilterGroup(filters: Filters): filters is FilterGroup {
  return 'operator' in filters;
}

/** Filters(存储形)→ 编辑草稿;{} → 单个空 AND 组。 */
export function filtersToDraft(filters: Filters): GroupDraft {
  const convert = (group: FilterGroup): GroupDraft => ({
    operator: group.operator,
    conditions: group.conditions.map((condition) => {
      if ('operator' in condition) {
        return convert(condition);
      }
      if ('field_kind' in condition) {
        return {
          field: `custom:${condition.field_def_id}`,
          op: condition.op,
          value: String(condition.value ?? ''),
        };
      }
      const value = condition.value;
      return {
        field: condition.field,
        op: condition.op,
        value: Array.isArray(value) ? value.join(',') : String(value ?? ''),
      };
    }),
  });
  if (!isFilterGroup(filters)) {
    return { operator: 'AND', conditions: [] };
  }
  return convert(filters);
}

/** 编辑草稿 → Filters(存储形);空条件组 → {}(不过滤)。 */
export function draftToFilters(draft: GroupDraft): Filters {
  const convertConditions = (
    conditions: ReadonlyArray<FieldConditionDraft | GroupDraft>,
  ): FilterCondition[] =>
    conditions.map((condition) => {
      if (isGroupDraft(condition)) {
        return {
          operator: condition.operator,
          conditions: convertConditions(condition.conditions),
        };
      }
      const listOps: readonly string[] = ['in', 'not_in'];
      const nullOps: readonly string[] = ['is_null', 'is_not_null'];
      if (nullOps.includes(condition.op)) {
        return { field: condition.field, op: condition.op };
      }
      if (listOps.includes(condition.op)) {
        return {
          field: condition.field,
          op: condition.op,
          value: condition.value
            .split(',')
            .map((part) => part.trim())
            .filter((part) => part !== ''),
        };
      }
      return { field: condition.field, op: condition.op, value: condition.value };
    });
  if (draft.conditions.length === 0) return {};
  return { operator: draft.operator, conditions: convertConditions(draft.conditions) };
}

function countConditions(draft: GroupDraft): number {
  return draft.conditions.reduce(
    (acc, condition) => acc + (isGroupDraft(condition) ? countConditions(condition) : 1),
    0,
  );
}

function groupDepth(draft: GroupDraft): number {
  return draft.conditions.reduce(
    (acc, condition) =>
      Math.max(acc, isGroupDraft(condition) ? groupDepth(condition) + 1 : 1),
    1,
  );
}

interface FilterGroupEditorProps {
  readonly draft: GroupDraft;
  readonly depth: number;
  readonly onChange: (next: GroupDraft) => void;
}

function FilterGroupEditor(props: FilterGroupEditorProps): React.JSX.Element {
  const { draft, depth, onChange } = props;
  const t = useT();
  const MAX_DEPTH = 3;

  const replaceAt = (index: number, next: FieldConditionDraft | GroupDraft): void => {
    const conditions = draft.conditions.map((item, i) => (i === index ? next : item));
    onChange({ ...draft, conditions });
  };
  const removeAt = (index: number): void => {
    onChange({ ...draft, conditions: draft.conditions.filter((_, i) => i !== index) });
  };

  return (
    <fieldset className="mesh-filter-panel__group">
      <legend className="mesh-filter-panel__legend">
        <select
          aria-label={t('board.filterOperatorLabel')}
          value={draft.operator}
          onChange={(event) =>
            onChange({ ...draft, operator: event.target.value as 'AND' | 'OR' })
          }
          data-testid="filter-operator"
        >
          <option value="AND">AND</option>
          <option value="OR">OR</option>
        </select>
      </legend>
      {draft.conditions.map((condition, index) =>
        isGroupDraft(condition) ? (
          <div key={`g${index}`} className="mesh-filter-panel__nested">
            <FilterGroupEditor
              draft={condition}
              depth={depth + 1}
              onChange={(next) => replaceAt(index, next)}
            />
            <Button variant="secondary" onClick={() => removeAt(index)} aria-label={t('board.filterRemoveGroup')}>
              {t('board.filterRemoveGroup')}
            </Button>
          </div>
        ) : (
          <div key={`c${index}`} className="mesh-filter-panel__row" data-testid={`filter-row-${index}`}>
            <Select
              label={t('board.filterFieldLabel')}
              value={condition.field}
              onChange={(event) => replaceAt(index, { ...condition, field: event.target.value })}
            >
              {FILTER_FIELD_OPTIONS.map((field) => (
                <option key={field} value={field}>
                  {t('board.filterField.' + field)}
                </option>
              ))}
            </Select>
            <Select
              label={t('board.filterOpLabel')}
              value={condition.op}
              onChange={(event) =>
                replaceAt(index, { ...condition, op: event.target.value as FilterOp })
              }
            >
              {OP_OPTIONS.map((op) => (
                <option key={op} value={op}>
                  {op}
                </option>
              ))}
            </Select>
            <Input
              label={t('board.filterValueLabel')}
              value={condition.value}
              placeholder={t('board.filterValueHint')}
              onChange={(event) => replaceAt(index, { ...condition, value: event.target.value })}
            />
            <Button variant="secondary" onClick={() => removeAt(index)} aria-label={t('board.filterRemoveCondition')}>
              <Icon name="close" size={16} />
            </Button>
          </div>
        ),
      )}
      <div className="mesh-filter-panel__actions">
        <Button
          variant="secondary"
          data-testid="filter-add-condition"
          onClick={() =>
            onChange({
              ...draft,
              conditions: [...draft.conditions, { field: 'priority', op: 'eq', value: '' }],
            })
          }
        >
          + {t('board.filterAddCondition')}
        </Button>
        {depth < MAX_DEPTH ? (
          <Button
            variant="secondary"
            data-testid="filter-add-group"
            onClick={() =>
              onChange({
                ...draft,
                conditions: [
                  ...draft.conditions,
                  { operator: draft.operator === 'AND' ? 'OR' : 'AND', conditions: [] },
                ],
              })
            }
          >
            + {t('board.filterAddGroup')}
          </Button>
        ) : null}
      </div>
    </fieldset>
  );
}

interface FilterConfigPanelProps {
  readonly filters: Filters;
  readonly onChange: (next: Filters) => void;
}

export function FilterConfigPanel(props: FilterConfigPanelProps): React.JSX.Element {
  const { filters, onChange } = props;
  const t = useT();
  const draft = filtersToDraft(filters);
  const tooComplex = countConditions(draft) > 20 || groupDepth(draft) > 3;
  return (
    <div className="mesh-filter-panel" data-testid="filter-config-panel">
      <FilterGroupEditor draft={draft} depth={1} onChange={(next) => onChange(draftToFilters(next))} />
      <p className="mesh-filter-panel__meta">
        {t('board.filterPreview', { count: '—' })}
        {tooComplex ? <strong> {t('board.filterTooComplex')}</strong> : null}
      </p>
    </div>
  );
}
