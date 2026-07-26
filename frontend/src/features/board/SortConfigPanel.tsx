/**
 * 排序配置面板(kanban.md §2.3 sort 结构):有序规则数组,前者优先;
 * 行级字段 + 方向 + 上移/下移/删除。
 */
/* eslint-disable react-refresh/only-export-components -- SORT_FIELD_OPTIONS 与组件同模块契约 */
import { Button, Select } from '../../design';
import { useT } from '../../i18n';
import type { SortRule } from './types';

export const SORT_FIELD_OPTIONS: readonly string[] = [
  'position',
  'priority',
  'due_date',
  'start_date',
  'created_at',
  'updated_at',
  'status_id',
];

interface SortConfigPanelProps {
  readonly rules: readonly SortRule[];
  readonly onChange: (next: readonly SortRule[]) => void;
}

export function SortConfigPanel(props: SortConfigPanelProps): React.JSX.Element {
  const { rules, onChange } = props;
  const t = useT();

  const replaceAt = (index: number, next: SortRule): void => {
    onChange(rules.map((rule, i) => (i === index ? next : rule)));
  };
  const removeAt = (index: number): void => {
    onChange(rules.filter((_, i) => i !== index));
  };
  const move = (index: number, delta: number): void => {
    const target = index + delta;
    if (target < 0 || target >= rules.length) return;
    const next = [...rules];
    const [rule] = next.splice(index, 1);
    if (rule !== undefined) {
      next.splice(target, 0, rule);
      onChange(next);
    }
  };

  return (
    <div className="mesh-sort-panel" data-testid="sort-config-panel">
      {rules.length === 0 ? (
        <p className="mesh-sort-panel__empty">{t('board.sortEmpty')}</p>
      ) : null}
      {rules.map((rule, index) => (
        <div key={`${rule.field ?? 'custom'}-${index}`} className="mesh-sort-panel__row" data-testid={`sort-row-${index}`}>
          <Select
            label={t('board.sortFieldLabel')}
            value={rule.field ?? ''}
            onChange={(event) => replaceAt(index, { ...rule, field: event.target.value })}
          >
            {SORT_FIELD_OPTIONS.map((field) => (
              <option key={field} value={field}>
                {t('board.sortField.' + field)}
              </option>
            ))}
          </Select>
          <Select
            label={t('board.sortOrderLabel')}
            value={rule.order}
            onChange={(event) =>
              replaceAt(index, { ...rule, order: event.target.value as 'asc' | 'desc' })
            }
          >
            <option value="asc">{t('board.sortAsc')}</option>
            <option value="desc">{t('board.sortDesc')}</option>
          </Select>
          <Button variant="secondary" onClick={() => move(index, -1)} aria-label={t('board.sortMoveUp')}>
            ↑
          </Button>
          <Button variant="secondary" onClick={() => move(index, 1)} aria-label={t('board.sortMoveDown')}>
            ↓
          </Button>
          <Button variant="secondary" onClick={() => removeAt(index)} aria-label={t('board.sortRemove')}>
            ✕
          </Button>
        </div>
      ))}
      <Button
        variant="secondary"
        data-testid="sort-add"
        onClick={() => onChange([...rules, { field: 'position', order: 'asc' }])}
      >
        + {t('board.sortAdd')}
      </Button>
    </div>
  );
}
