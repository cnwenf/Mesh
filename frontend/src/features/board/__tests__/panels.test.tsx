/**
 * 配置面板组件测试:FilterConfigPanel(条件编辑 + 草稿互转)、SortConfigPanel
 * (增删移)、WipConfigPanel(limit 解析与移除)、ViewSwitcher(选中与菜单)。
 */
import { useState } from 'react';
import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import {
  FilterConfigPanel,
  draftToFilters,
  filtersToDraft,
} from '../FilterConfigPanel';
import { SortConfigPanel } from '../SortConfigPanel';
import { ViewSwitcher } from '../ViewSwitcher';
import { WipConfigPanel } from '../WipConfigPanel';
import type { Filters, SortRule, View } from '../types';

describe('filters 草稿互转', () => {
  it('空 filters → 空 AND 组 → 空 filters 往返', () => {
    const draft = filtersToDraft({});
    expect(draft).toEqual({ operator: 'AND', conditions: [] });
    expect(draftToFilters(draft)).toEqual({});
  });

  it('条件往返(in 列表值用逗号分隔编辑)', () => {
    const filters = {
      operator: 'AND' as const,
      conditions: [
        { field: 'priority', op: 'in' as const, value: ['high', 'urgent'] },
        { field: 'due_date', op: 'is_null' as const },
      ],
    };
    const draft = filtersToDraft(filters);
    expect(draft.conditions).toHaveLength(2);
    expect(draftToFilters(draft)).toEqual({
      operator: 'AND',
      conditions: [
        { field: 'priority', op: 'in', value: ['high', 'urgent'] },
        { field: 'due_date', op: 'is_null' },
      ],
    });
  });
});

describe('FilterConfigPanel', () => {
  it('添加条件并经 onChange 上报结构化 filters', () => {
    const onChange = vi.fn();
    renderWithProviders(<FilterConfigPanel filters={{}} onChange={onChange} />);
    fireEvent.click(screen.getByTestId('filter-add-condition'));
    expect(onChange).toHaveBeenCalledWith({
      operator: 'AND',
      conditions: [{ field: 'priority', op: 'eq', value: '' }],
    });
  });

  it('添加条件组(嵌套)并移除', () => {
    // 受控面板:以状态包装组件驱动重渲染,模拟父组件接收 onChange 后回填。
    const seen: unknown[] = [];
    function Stateful() {
      const [filters, setFilters] = useState<Filters>({});
      return (
        <FilterConfigPanel
          filters={filters}
          onChange={(next) => {
            seen.push(next);
            setFilters(next);
          }}
        />
      );
    }
    renderWithProviders(<Stateful />);
    fireEvent.click(screen.getByTestId('filter-add-group'));
    expect((seen.at(-1) as { conditions: unknown[] }).conditions).toHaveLength(1);
    fireEvent.click(screen.getByText('Remove group'));
    expect(seen.at(-1)).toEqual({});
  });
});

describe('SortConfigPanel', () => {
  it('增/删/移动排序规则', () => {
    const seen: ReadonlyArray<SortRule>[] = [];
    function Stateful() {
      const [rules, setRules] = useState<readonly SortRule[]>([]);
      return (
        <SortConfigPanel
          rules={rules}
          onChange={(next) => {
            seen.push(next);
            setRules(next);
          }}
        />
      );
    }
    renderWithProviders(<Stateful />);
    expect(screen.getByText(/No sort rules/i)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('sort-add'));
    expect(seen.at(-1)).toEqual([{ field: 'position', order: 'asc' }]);

    // 再加一条并上移第二条 → 新规则在前
    fireEvent.click(screen.getByTestId('sort-add'));
    const rows = screen.getAllByTestId(/^sort-row-/);
    fireEvent.click(within(rows[1] as HTMLElement).getByRole('button', { name: 'Move up' }));
    expect(seen.at(-1)).toEqual([
      { field: 'position', order: 'asc' },
      { field: 'position', order: 'asc' },
    ]);
    // 删除第一条
    fireEvent.click(within(screen.getAllByTestId(/^sort-row-/)[0] as HTMLElement).getByRole('button', { name: 'Remove sort rule' }));
    expect(seen.at(-1)).toHaveLength(1);
  });
});

describe('WipConfigPanel', () => {
  const columns = [
    { key: 'in_progress', label: 'board.category.in_progress', collapsed: false, wip: { limit: 5, enforcement: 'warn' as const }, count: 0, placeholder: false },
    { key: 'todo', label: 'board.category.todo', collapsed: false, wip: null, count: 0, placeholder: false },
  ];

  it('保存 limit + enforcement 调用 onSave', async () => {
    const onSave = vi.fn(async () => undefined);
    renderWithProviders(<WipConfigPanel columns={columns} onSave={onSave} />);
    fireEvent.change(screen.getByTestId('wip-limit-in_progress'), { target: { value: '7' } });
    fireEvent.change(screen.getByTestId('wip-enforcement-in_progress'), { target: { value: 'block' } });
    fireEvent.click(screen.getByTestId('wip-save-in_progress'));
    expect(onSave).toHaveBeenCalledWith('in_progress', 7, 'block');
  });

  it('清空 limit → null(移除规则)', () => {
    const onSave = vi.fn(async () => undefined);
    renderWithProviders(<WipConfigPanel columns={columns} onSave={onSave} />);
    fireEvent.change(screen.getByTestId('wip-limit-in_progress'), { target: { value: '' } });
    fireEvent.click(screen.getByTestId('wip-save-in_progress'));
    expect(onSave).toHaveBeenCalledWith('in_progress', null, 'warn');
  });

  it('非法 limit(0/负数/非数字)不触发保存', () => {
    const onSave = vi.fn(async () => undefined);
    renderWithProviders(<WipConfigPanel columns={columns} onSave={onSave} />);
    fireEvent.change(screen.getByTestId('wip-limit-todo'), { target: { value: '0' } });
    fireEvent.click(screen.getByTestId('wip-save-todo'));
    expect(onSave).not.toHaveBeenCalled();
  });
});

describe('ViewSwitcher', () => {
  const views: View[] = [
    {
      id: 'v1', workspace_id: 'ws', project_id: null, owner_member_id: 'm1', name: 'One',
      layout: 'board', visibility: 'private', filters: {}, group_by: null, sub_group_by: null,
      sort: [], display_fields: [], board_settings: {}, position: 1, is_default: true,
      created_at: '', updated_at: '', can_write: true,
    },
    {
      id: 'v2', workspace_id: 'ws', project_id: null, owner_member_id: 'm2', name: 'Two',
      layout: 'list', visibility: 'shared', filters: {}, group_by: null, sub_group_by: null,
      sort: [], display_fields: [], board_settings: {}, position: 2, is_default: false,
      created_at: '', updated_at: '', can_write: false,
    },
  ];

  function renderSwitcher() {
    return renderWithProviders(
      <ViewSwitcher
        views={views}
        selectedId="v1"
        canWrite={(view) => view.can_write === true}
        onSelect={vi.fn()}
        onCreate={vi.fn(async () => undefined)}
        onRename={vi.fn(async () => undefined)}
        onDuplicate={vi.fn(async () => undefined)}
        onSetDefault={vi.fn(async () => undefined)}
        onDelete={vi.fn(async () => undefined)}
      />,
    );
  }

  it('当前视图高亮且默认视图带星标;无写权限视图不呈现菜单', () => {
    renderSwitcher();
    const active = screen.getByTestId('view-entry-v1');
    expect(active.className).toContain('--active');
    // 默认视图星标由统一 Icon(star 实心 + label)呈现,字符「★」已移除
    const defaultMarker = within(active).getByTitle('Default view');
    expect(defaultMarker.querySelector('svg')).not.toBeNull();
    expect(within(active).getByRole('img', { name: 'Default view' })).toBeInTheDocument();
    expect(within(active).queryByText('★')).not.toBeInTheDocument();
    expect(screen.getByTestId('view-menu-v1')).toBeInTheDocument();
    expect(screen.queryByTestId('view-menu-v2')).not.toBeInTheDocument();
  });

  it('菜单提供重命名/复制/删除;默认视图不再呈现「设为默认」', () => {
    renderSwitcher();
    fireEvent.click(screen.getByTestId('view-menu-v1'));
    const menu = screen.getByTestId('view-menu-list-v1');
    expect(within(menu).getByText('Rename')).toBeInTheDocument();
    expect(within(menu).getByText('Duplicate')).toBeInTheDocument();
    expect(within(menu).getByText('Delete')).toBeInTheDocument();
    expect(within(menu).queryByText('Set as default')).not.toBeInTheDocument();
  });
});
