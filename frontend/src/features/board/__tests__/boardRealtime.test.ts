/**
 * 看板实时增量合并测试(纯函数,kanban.md §3.5):单卡插入/移动/移除、防回退、
 * view.updated → 整板重拉、view.presence 不动分组、归属本地重判。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import type { CustomFieldDef, CustomFieldType } from '../../labels/types';
import type { BoardCard, BoardGroup, BoardLane, BoardProjectionColumn } from '../projection';
import {
  applyBoardFrame,
  applyBoardLaneFrame,
  applyCustomFieldDefinitionToGroups,
  applyCustomFieldDefinitionToLanes,
  applyLabelDefinitionToGroups,
  applyLabelDefinitionToLanes,
  cardBelongsToView,
  groupKeysForCard,
  groupKeyForCard,
  mergeBoardCardForRealtime,
  rebucketGroups,
  NONE_KEY,
} from '../boardRealtime';

function card(overrides: Partial<BoardCard> = {}): BoardCard {
  return {
    id: 'i1',
    identifier: 'WEB-1',
    title: 'Card',
    state_category: 'todo',
    status: { id: 'st_todo', name: 'Todo', category: 'todo' },
    status_id: 'st_todo',
    priority: 'high',
    assignee: null,
    assignee_id: null,
    project_id: null,
    position: 1,
    version: 1,
    updated_at: '2026-07-26T10:00:00Z',
    ...overrides,
  };
}

function groups(): BoardGroup[] {
  return [
    { key: 'todo', label: 'Todo', count: 1, wip: null, data: [card()] },
    {
      key: 'in_progress',
      label: 'In Progress',
      count: 0,
      wip: { limit: 2, enforcement: 'block' },
      data: [],
    },
  ];
}

function frame(event: string, payload: Record<string, unknown>): RealtimeEventFrame {
  return { op: 'event', channel: 'workspace:w:issues', seq: 1, event, payload };
}

const CTX = { groupBy: 'state_category', belongs: () => true };

const SWIMLANE_COLUMNS: readonly BoardProjectionColumn[] = [
  { key: 'todo', label: 'Todo', count: 1, wip: null },
  { key: 'done', label: 'Done', count: 0, wip: null },
];

function lanes(): BoardLane[] {
  return [
    {
      key: 'high',
      label: 'High',
      count: 1,
      groups: [
        { key: 'todo', count: 1, data: [card()] },
        { key: 'done', count: 0, data: [] },
      ],
    },
    {
      key: 'low',
      label: 'Low',
      count: 0,
      groups: [
        { key: 'todo', count: 0, data: [] },
        { key: 'done', count: 0, data: [] },
      ],
    },
  ];
}

function singleCell(
  item: BoardCard,
  groupKey: string,
  subGroupKey: string,
): { columns: BoardProjectionColumn[]; lanes: BoardLane[] } {
  return {
    columns: [{ key: groupKey, label: groupKey, count: 1, wip: null }],
    lanes: [
      {
        key: subGroupKey,
        label: subGroupKey,
        count: 1,
        groups: [{ key: groupKey, count: 1, data: [item] }],
      },
    ],
  };
}

const LANE_CTX = {
  groupBy: 'state_category',
  subGroupBy: 'priority',
  belongs: () => true,
};

function customField(
  type: CustomFieldType,
  overrides: Partial<CustomFieldDef> = {},
): CustomFieldDef {
  return {
    id: 'field-a',
    workspace_id: 'w',
    project_id: null,
    name: 'Teams',
    field_key: 'teams',
    type,
    is_required: false,
    required_on: [],
    default_value: null,
    config: {},
    position: 0,
    is_active: true,
    options: [
      {
        id: 'option-a',
        field_def_id: 'field-a',
        name: 'Backend',
        color: null,
        position: 0,
        is_active: true,
        created_at: '',
        updated_at: '',
      },
    ],
    created_at: '',
    updated_at: '',
    ...overrides,
  };
}

describe('groupKeyForCard(§2.4)', () => {
  it('按 group_by 派生列 key', () => {
    const c = card({ assignee_id: 'm1', project_id: 'p1', priority: 'urgent' });
    expect(groupKeyForCard(c, 'state_category')).toBe('todo');
    expect(groupKeyForCard(c, 'status')).toBe('st_todo');
    expect(groupKeyForCard(c, 'assignee')).toBe('m1');
    expect(groupKeyForCard(c, 'priority')).toBe('urgent');
    expect(groupKeyForCard(c, 'project')).toBe('p1');
  });

  it('assignee/project 无值 → __none__', () => {
    const c = card();
    expect(groupKeyForCard(c, 'assignee')).toBe(NONE_KEY);
    expect(groupKeyForCard(c, 'project')).toBe(NONE_KEY);
  });

  it('label 与 multi_select 返回完整去重 memberships', () => {
    const fieldId = '11111111-1111-4111-8111-111111111111';
    const c = card({
      labels: [
        { id: 'label-a', name: 'A', color: '#111111' },
        { id: 'label-b', name: 'B', color: '#222222' },
      ],
      custom_field_values: [
        { field_def_id: fieldId, value_json: ['option-a', 'option-b', 'option-a'] },
      ],
    });
    const definition = {
      id: fieldId,
      workspace_id: 'w',
      project_id: null,
      name: 'Teams',
      field_key: 'teams',
      type: 'multi_select' as const,
      is_required: false,
      required_on: [],
      default_value: null,
      config: {},
      position: 0,
      is_active: true,
      options: [],
      created_at: '',
      updated_at: '',
    };
    expect(groupKeysForCard(c, 'label')).toEqual(['label-a', 'label-b']);
    expect(groupKeysForCard(c, fieldId, [definition])).toEqual(['option-a', 'option-b']);
  });

  it.each([
    ['text', { value_text: 'Alpha' }, 'Alpha'],
    ['textarea', { value_text: 'Long form' }, 'Long form'],
    ['url', { value_text: 'https://mesh.test' }, 'https://mesh.test'],
    ['number', { value_number: 42 }, '42'],
    ['date', { value_date: '2026-08-05' }, '2026-08-05'],
    ['datetime', { value_date: '2026-08-05T12:00:00Z' }, '2026-08-05T12:00:00Z'],
    ['member', { value_member_id: 'member-a' }, 'member-a'],
    ['boolean', { value_boolean: false }, 'false'],
    ['single_select', { value_json: 'option-a' }, 'option-a'],
  ] as const)('按 %s 定义从类型化快照派生分组 key', (type, value, expected) => {
    const item = card({
      custom_field_values: [{ field_def_id: 'field-a', ...value }],
    });
    expect(groupKeysForCard(item, 'field-a', [customField(type)])).toEqual([expected]);
  });

  it('动态字段缺定义时仍兼容投影快照类型，并把空值归入 none', () => {
    const keyFor = (value: Record<string, unknown>) =>
      groupKeysForCard(
        card({ custom_field_values: [{ field_def_id: 'field-a', ...value }] }),
        'field-a',
      );

    expect(keyFor({ value_json: ['a', 1, 'b', 'a'] })).toEqual(['a', 'b']);
    expect(keyFor({ value_json: 'single' })).toEqual(['single']);
    expect(keyFor({ value_text: 'text' })).toEqual(['text']);
    expect(keyFor({ value_number: 3 })).toEqual(['3']);
    expect(keyFor({ value_date: '2026-08-05' })).toEqual(['2026-08-05']);
    expect(keyFor({ value_member_id: 'member-a' })).toEqual(['member-a']);
    expect(keyFor({ value_boolean: true })).toEqual(['true']);
    expect(keyFor({ value_json: [null, 1] })).toEqual([NONE_KEY]);
    expect(groupKeysForCard(card(), 'field-a')).toEqual([NONE_KEY]);
    expect(groupKeysForCard(card(), 'label')).toEqual([NONE_KEY]);
  });

  it('multi_select 去重有效字符串，类型不匹配时归入 none', () => {
    const definition = customField('multi_select');
    expect(
      groupKeysForCard(
        card({
          custom_field_values: [
            { field_def_id: 'field-a', value_json: ['option-a', 1, 'option-a'] },
          ],
        }),
        'field-a',
        [definition],
      ),
    ).toEqual(['option-a']);
    expect(
      groupKeysForCard(
        card({ custom_field_values: [{ field_def_id: 'field-a', value_json: 42 }] }),
        'field-a',
        [definition],
      ),
    ).toEqual([NONE_KEY]);

    for (const definitionWithoutMatchingValue of [
      customField('text'),
      customField('number'),
      customField('date'),
      customField('member'),
      customField('boolean'),
      customField('single_select'),
    ]) {
      expect(
        groupKeysForCard(
          card({ custom_field_values: [{ field_def_id: 'field-a', value_json: null }] }),
          'field-a',
          [definitionWithoutMatchingValue],
        ),
      ).toEqual([NONE_KEY]);
    }
  });
});

describe('mergeBoardCardForRealtime 关联快照', () => {
  it('custom_field_changed 替换目标值且不把帧元字段扩散进卡片', () => {
    const merged = mergeBoardCardForRealtime(
      card({
        custom_field_values: [
          { field_def_id: 'field-a', value_text: 'old' },
          { field_def_id: 'field-b', value_number: 1 },
        ],
      }),
      frame('custom_field_changed', {
        issue_id: 'i1',
        field_def_id: 'field-a',
        field_key: 'teams',
        value: { value_text: 'new' },
        changes: { title: 'Updated' },
      }),
    );

    expect(merged.title).toBe('Updated');
    expect(merged.custom_field_values).toEqual([
      { field_def_id: 'field-b', value_number: 1 },
      { field_def_id: 'field-a', value_text: 'new' },
    ]);
    expect(merged).not.toHaveProperty('issue_id');
    expect(merged).not.toHaveProperty('field_key');
    expect(merged).not.toHaveProperty('value');
  });

  it('无对象值表示清除字段；缺少 field_def_id 时保持字段快照', () => {
    const existing = card({
      custom_field_values: [{ field_def_id: 'field-a', value_text: 'old' }],
    });
    expect(
      mergeBoardCardForRealtime(
        existing,
        frame('custom_field_changed', { field_def_id: 'field-a', value: null }),
      ).custom_field_values,
    ).toEqual([]);
    expect(
      mergeBoardCardForRealtime(existing, frame('custom_field_changed', { value: {} }))
        .custom_field_values,
    ).toEqual(existing.custom_field_values);
  });
});

describe('动态轴实时 memberships', () => {
  it('label 全量集合按差集增删 placements，普通 updated 保留多列', () => {
    const labelled = card({
      labels: [{ id: 'label-a', name: 'A', color: '#111111' }],
    });
    const base: BoardGroup[] = [
      { key: 'label-a', label: 'A', count: 1, wip: null, data: [labelled] },
      { key: 'label-b', label: 'B', count: 0, wip: null, data: [] },
      { key: NONE_KEY, label: 'No label', count: 0, wip: null, data: [] },
    ];
    const associated = applyBoardFrame(
      base,
      frame('issue.labels_changed', {
        issue_id: 'i1',
        labels: [
          { id: 'label-a', name: 'A', color: '#111111' },
          { id: 'label-b', name: 'B', color: '#222222' },
        ],
      }),
      { groupBy: 'label', belongs: () => true },
    );
    expect(associated.groups.map((group) => [group.key, group.count])).toEqual([
      ['label-a', 1],
      ['label-b', 1],
      [NONE_KEY, 0],
    ]);

    const updated = applyBoardFrame(
      associated.groups,
      frame('issue.updated', { id: 'i1', changes: { title: 'Patched' } }),
      { groupBy: 'label', belongs: () => true },
    );
    expect(updated.groups.filter((group) => group.data[0]?.title === 'Patched')).toHaveLength(2);
  });

  it('关联帧早于一维定义骨架时派生名称并保持 __none__ 最后', () => {
    const item = card({
      labels: [{ id: 'label-a', name: 'Alpha', color: '#111111' }],
      custom_field_values: [{ field_def_id: 'field-a', value_json: ['option-a'] }],
    });
    const labelResult = applyBoardFrame(
      [
        { key: 'label-a', label: 'Alpha', count: 1, wip: null, data: [item] },
        { key: NONE_KEY, label: 'No label', count: 0, wip: null, data: [] },
      ],
      frame('issue.labels_changed', {
        issue_id: item.id,
        labels: [
          { id: 'label-a', name: 'Alpha', color: '#111111' },
          { id: 'label-b', name: 'Beta', color: '#222222' },
        ],
      }),
      { groupBy: 'label', belongs: () => true },
    );
    expect(labelResult.groups.map((group) => [group.key, group.label])).toEqual([
      ['label-a', 'Alpha'],
      ['label-b', 'Beta'],
      [NONE_KEY, 'No label'],
    ]);

    const definition = customField('multi_select', {
      options: [
        {
          id: 'option-a',
          field_def_id: 'field-a',
          name: 'Alpha',
          color: null,
          position: 0,
          is_active: true,
          created_at: '',
          updated_at: '',
        },
        {
          id: 'option-b',
          field_def_id: 'field-a',
          name: 'Beta',
          color: null,
          position: 1,
          is_active: true,
          created_at: '',
          updated_at: '',
        },
      ],
    });
    const customResult = applyBoardFrame(
      [
        { key: 'option-a', label: 'Alpha', count: 1, wip: null, data: [item] },
        { key: NONE_KEY, label: 'No Teams', count: 0, wip: null, data: [] },
      ],
      frame('issue.custom_field_changed', {
        issue_id: item.id,
        field_def_id: definition.id,
        value: { value_json: ['option-a', 'option-b'] },
      }),
      { groupBy: definition.id, customFields: [definition], belongs: () => true },
    );
    expect(customResult.groups.map((group) => [group.key, group.label])).toEqual([
      ['option-a', 'Alpha'],
      ['option-b', 'Beta'],
      [NONE_KEY, 'No Teams'],
    ]);
  });

  it('双多值轴生成笛卡尔 placements，但 column/lane count 按 issue distinct', () => {
    const fieldId = '11111111-1111-4111-8111-111111111111';
    const definition = {
      id: fieldId,
      workspace_id: 'w',
      project_id: null,
      name: 'Teams',
      field_key: 'teams',
      type: 'multi_select' as const,
      is_required: false,
      required_on: [],
      default_value: null,
      config: {},
      position: 0,
      is_active: true,
      options: [],
      created_at: '',
      updated_at: '',
    };
    const item = card({
      labels: [{ id: 'label-a', name: 'A', color: '#111111' }],
      custom_field_values: [{ field_def_id: fieldId, value_json: ['option-x', 'option-y'] }],
    });
    const columns: BoardProjectionColumn[] = [
      { key: 'label-a', label: 'A', count: 1, wip: null },
      { key: 'label-b', label: 'B', count: 0, wip: null },
      { key: NONE_KEY, label: 'No label', count: 0, wip: null },
    ];
    const dynamicLanes: BoardLane[] = [
      {
        key: 'option-x',
        label: 'X',
        count: 1,
        groups: [
          { key: 'label-a', count: 1, data: [item] },
          { key: 'label-b', count: 0, data: [] },
          { key: NONE_KEY, count: 0, data: [] },
        ],
      },
      {
        key: 'option-y',
        label: 'Y',
        count: 1,
        groups: [
          { key: 'label-a', count: 1, data: [item] },
          { key: 'label-b', count: 0, data: [] },
          { key: NONE_KEY, count: 0, data: [] },
        ],
      },
    ];
    const result = applyBoardLaneFrame(
      columns,
      dynamicLanes,
      frame('issue.labels_changed', {
        issue_id: 'i1',
        labels: [
          { id: 'label-a', name: 'A', color: '#111111' },
          { id: 'label-b', name: 'B', color: '#222222' },
        ],
      }),
      { groupBy: 'label', subGroupBy: fieldId, customFields: [definition], belongs: () => true },
    );
    expect(result.columns.map((column) => [column.key, column.count])).toEqual([
      ['label-a', 1],
      ['label-b', 1],
      [NONE_KEY, 0],
    ]);
    expect(result.lanes.map((lane) => [lane.key, lane.count])).toEqual([
      ['option-x', 1],
      ['option-y', 1],
    ]);
    expect(result.lanes.flatMap((lane) => lane.groups.flatMap((group) => group.data))).toHaveLength(
      4,
    );
  });

  it('关联帧早于二维定义骨架时为新列/泳道派生名称并保持 __none__ 最后', () => {
    const definition = customField('multi_select', {
      options: [
        {
          id: 'option-a',
          field_def_id: 'field-a',
          name: 'Alpha team',
          color: null,
          position: 0,
          is_active: true,
          created_at: '',
          updated_at: '',
        },
        {
          id: 'option-b',
          field_def_id: 'field-a',
          name: 'Beta team',
          color: null,
          position: 1,
          is_active: true,
          created_at: '',
          updated_at: '',
        },
      ],
    });
    const item = card({
      labels: [{ id: 'label-a', name: 'Alpha', color: '#111111' }],
      custom_field_values: [{ field_def_id: definition.id, value_json: ['option-a'] }],
    });
    const columns: BoardProjectionColumn[] = [
      { key: 'label-a', label: 'Alpha', count: 1, wip: null },
      { key: NONE_KEY, label: 'No label', count: 0, wip: null },
    ];
    const dynamicLanes: BoardLane[] = [
      {
        key: 'option-a',
        label: 'Alpha team',
        count: 1,
        groups: [
          { key: 'label-a', count: 1, data: [item] },
          { key: NONE_KEY, count: 0, data: [] },
        ],
      },
      {
        key: NONE_KEY,
        label: 'No Teams',
        count: 0,
        groups: [
          { key: 'label-a', count: 0, data: [] },
          { key: NONE_KEY, count: 0, data: [] },
        ],
      },
    ];
    const context = {
      groupBy: 'label',
      subGroupBy: definition.id,
      customFields: [definition],
      belongs: () => true,
    };

    const withLabel = applyBoardLaneFrame(
      columns,
      dynamicLanes,
      frame('issue.labels_changed', {
        issue_id: item.id,
        labels: [
          { id: 'label-a', name: 'Alpha', color: '#111111' },
          { id: 'label-b', name: 'Beta', color: '#222222' },
        ],
      }),
      context,
    );
    expect(withLabel.columns.map((column) => [column.key, column.label])).toEqual([
      ['label-a', 'Alpha'],
      ['label-b', 'Beta'],
      [NONE_KEY, 'No label'],
    ]);

    const withCustom = applyBoardLaneFrame(
      withLabel.columns,
      withLabel.lanes,
      frame('issue.custom_field_changed', {
        issue_id: item.id,
        field_def_id: definition.id,
        value: { value_json: ['option-a', 'option-b'] },
      }),
      context,
    );
    expect(withCustom.lanes.map((lane) => [lane.key, lane.label])).toEqual([
      ['option-a', 'Alpha team'],
      ['option-b', 'Beta team'],
      [NONE_KEY, 'No Teams'],
    ]);
  });

  it('label 定义 created/updated/deleted 局部维护 skeleton', () => {
    const base: BoardGroup[] = [
      { key: NONE_KEY, label: 'No label', count: 0, wip: null, data: [] },
    ];
    const created = applyLabelDefinitionToGroups(
      base,
      frame('label.created', { id: 'label-a', name: 'Backend', color: '#123456' }),
    );
    expect(created.map((group) => [group.key, group.label])).toEqual([
      ['label-a', 'Backend'],
      [NONE_KEY, 'No label'],
    ]);
    const updated = applyLabelDefinitionToGroups(
      created,
      frame('label.updated', { id: 'label-a', name: 'Platform', color: '#654321' }),
    );
    expect(updated[0]?.label).toBe('Platform');
    expect(
      applyLabelDefinitionToGroups(updated, frame('label.deleted', { id: 'label-a' })),
    ).toEqual(base);
  });

  it('label 定义创建与重命名后按显示名/稳定 key 排序，none 恒在末尾', () => {
    const base: BoardGroup[] = [
      { key: 'label-z', label: 'Zulu', count: 0, wip: null, data: [] },
      { key: 'label-m', label: 'Mike', count: 0, wip: null, data: [] },
      { key: NONE_KEY, label: 'No label', count: 0, wip: null, data: [] },
    ];

    const created = applyLabelDefinitionToGroups(
      base,
      frame('label.created', { id: 'label-a', name: 'Alpha' }),
    );
    expect(created.map((group) => group.key)).toEqual([
      'label-a',
      'label-m',
      'label-z',
      NONE_KEY,
    ]);

    const renamed = applyLabelDefinitionToGroups(
      created,
      frame('label.updated', { id: 'label-z', name: 'Bravo' }),
    );
    expect(renamed.map((group) => group.key)).toEqual([
      'label-a',
      'label-z',
      'label-m',
      NONE_KEY,
    ]);
  });

  it('custom option updated 按名称/active 局部增删 skeleton', () => {
    const fieldId = '11111111-1111-4111-8111-111111111111';
    const base: BoardGroup[] = [
      { key: NONE_KEY, label: 'No Teams', count: 0, wip: null, data: [] },
    ];
    const created = applyCustomFieldDefinitionToGroups(
      base,
      frame('custom_field_option.updated', {
        field_def_id: fieldId,
        change: 'created',
        option: { id: 'option-a', name: 'Backend', is_active: true },
      }),
      fieldId,
    );
    expect(created.map((group) => [group.key, group.label])).toEqual([
      ['option-a', 'Backend'],
      [NONE_KEY, 'No Teams'],
    ]);
    const renamed = applyCustomFieldDefinitionToGroups(
      created,
      frame('custom_field_option.updated', {
        field_def_id: fieldId,
        change: 'updated',
        option: { id: 'option-a', name: 'Platform', is_active: true },
      }),
      fieldId,
    );
    expect(renamed[0]?.label).toBe('Platform');
    expect(
      applyCustomFieldDefinitionToGroups(
        renamed,
        frame('custom_field_option.updated', {
          field_def_id: fieldId,
          change: 'updated',
          option: { id: 'option-a', name: 'Platform', is_active: false },
        }),
        fieldId,
      ),
    ).toEqual(base);

    expect(
      applyCustomFieldDefinitionToGroups(
        created,
        frame('custom_field.updated', {
          id: fieldId,
          name: 'Teams',
          change: 'updated',
          is_active: false,
        }),
        fieldId,
      ),
    ).toEqual([]);
    expect(
      applyCustomFieldDefinitionToGroups(
        created,
        frame('custom_field.updated', { id: fieldId, change: 'deleted' }),
        fieldId,
      ),
    ).toEqual([]);
  });
});

describe('二维定义帧局部维护 skeleton', () => {
  it('label created/updated/deleted 同步双轴骨架、卡片快照与孤儿 none cell', () => {
    const labelled = card({
      labels: [{ id: 'label-a', name: 'Backend', color: '#111111' }],
    });
    const columns: BoardProjectionColumn[] = [
      { key: 'label-a', label: 'Backend', count: 1, wip: null },
      { key: NONE_KEY, label: 'No label', count: 0, wip: null },
    ];
    const labelLanes: BoardLane[] = [
      {
        key: 'label-a',
        label: 'Backend',
        count: 1,
        groups: [
          { key: 'label-a', count: 1, data: [labelled] },
          { key: NONE_KEY, count: 0, data: [] },
        ],
      },
      {
        key: NONE_KEY,
        label: 'No label',
        count: 0,
        groups: [
          { key: 'label-a', count: 0, data: [] },
          { key: NONE_KEY, count: 0, data: [] },
        ],
      },
    ];
    const context = { groupBy: 'label', subGroupBy: 'label', belongs: () => true };

    const created = applyLabelDefinitionToLanes(
      columns,
      labelLanes,
      frame('label.created', { id: 'label-b', name: 'Frontend', color: '#222222' }),
      context,
    );
    expect(created.columns.map((column) => column.key)).toEqual(['label-a', 'label-b', NONE_KEY]);
    expect(created.lanes.map((lane) => lane.key)).toEqual(['label-a', 'label-b', NONE_KEY]);
    expect(created.lanes.find((lane) => lane.key === 'label-b')?.groups).toHaveLength(3);

    const updated = applyLabelDefinitionToLanes(
      created.columns,
      created.lanes,
      frame('label.updated', { id: 'label-a', name: 'Platform', color: '#abcdef' }),
      context,
    );
    expect(updated.columns.find((column) => column.key === 'label-a')?.label).toBe('Platform');
    const updatedLane = updated.lanes.find((lane) => lane.key === 'label-a');
    expect(updatedLane?.label).toBe('Platform');
    expect(updatedLane?.groups.find((group) => group.key === 'label-a')?.data[0]?.labels).toEqual([
      { id: 'label-a', name: 'Platform', color: '#abcdef' },
    ]);

    const deleted = applyLabelDefinitionToLanes(
      updated.columns,
      updated.lanes,
      frame('label.deleted', { id: 'label-a' }),
      context,
    );
    expect(deleted.columns.map((column) => column.key)).toEqual(['label-b', NONE_KEY]);
    expect(deleted.lanes.map((lane) => lane.key)).toEqual(['label-b', NONE_KEY]);
    expect(
      deleted.lanes
        .find((lane) => lane.key === NONE_KEY)
        ?.groups.find((group) => group.key === NONE_KEY)?.data[0],
    ).toMatchObject({ id: 'i1', labels: [] });
  });

  it('label 非定义帧、非 label 轴及缺名称事件保持原投影引用', () => {
    const baseLanes = lanes();
    for (const [event, payload, context] of [
      ['comment.created', { id: 'c1' }, LANE_CTX],
      ['label.created', { id: 'label-a', name: 'Backend' }, LANE_CTX],
      ['label.created', { id: 'label-a' }, { ...LANE_CTX, groupBy: 'label' }],
      ['label.updated', { id: 'label-a' }, { ...LANE_CTX, subGroupBy: 'label' }],
      ['label.archived', { id: 'label-a' }, { ...LANE_CTX, groupBy: 'label' }],
    ] as const) {
      const result = applyLabelDefinitionToLanes(
        SWIMLANE_COLUMNS,
        baseLanes,
        frame(event, payload),
        context,
      );
      expect(result.columns).toBe(SWIMLANE_COLUMNS);
      expect(result.lanes).toBe(baseLanes);
    }
  });

  it('custom option created/renamed/deleted 同步双轴，删除后孤儿落入 none cell', () => {
    const item = card({
      custom_field_values: [{ field_def_id: 'field-a', value_json: 'option-a' }],
    });
    const columns: BoardProjectionColumn[] = [
      { key: 'option-a', label: 'Backend', count: 1, wip: null },
      { key: NONE_KEY, label: 'No Teams', count: 0, wip: null },
    ];
    const customLanes: BoardLane[] = [
      {
        key: 'option-a',
        label: 'Backend',
        count: 1,
        groups: [
          { key: 'option-a', count: 1, data: [item] },
          { key: NONE_KEY, count: 0, data: [] },
        ],
      },
      {
        key: NONE_KEY,
        label: 'No Teams',
        count: 0,
        groups: [
          { key: 'option-a', count: 0, data: [] },
          { key: NONE_KEY, count: 0, data: [] },
        ],
      },
    ];
    const context = {
      groupBy: 'field-a',
      subGroupBy: 'field-a',
      customFields: [customField('single_select')],
      belongs: () => true,
    };
    const optionFrame = (change: string, option: Record<string, unknown>) =>
      frame('custom_field_option.updated', { field_def_id: 'field-a', change, option });

    const created = applyCustomFieldDefinitionToLanes(
      columns,
      customLanes,
      optionFrame('created', { id: 'option-b', name: 'Frontend', is_active: true }),
      context,
    );
    expect(created.columns.map((column) => [column.key, column.label])).toEqual([
      ['option-a', 'Backend'],
      ['option-b', 'Frontend'],
      [NONE_KEY, 'No Teams'],
    ]);
    expect(created.lanes.map((lane) => lane.key)).toEqual(['option-a', 'option-b', NONE_KEY]);

    const renamed = applyCustomFieldDefinitionToLanes(
      created.columns,
      created.lanes,
      optionFrame('updated', { id: 'option-a', name: 'Platform', is_active: true }),
      context,
    );
    expect(renamed.columns.find((column) => column.key === 'option-a')?.label).toBe('Platform');
    expect(renamed.lanes.find((lane) => lane.key === 'option-a')?.label).toBe('Platform');

    const deleted = applyCustomFieldDefinitionToLanes(
      renamed.columns,
      renamed.lanes,
      optionFrame('deleted', { id: 'option-a', name: 'Platform', is_active: false }),
      context,
    );
    expect(deleted.columns.map((column) => column.key)).toEqual(['option-b', NONE_KEY]);
    expect(deleted.lanes.map((lane) => lane.key)).toEqual(['option-b', NONE_KEY]);
    expect(
      deleted.lanes
        .find((lane) => lane.key === NONE_KEY)
        ?.groups.find((group) => group.key === NONE_KEY)?.data[0]?.id,
    ).toBe('i1');
  });

  it('custom option create/position update uses definition order in one and two dimensions', () => {
    const field = customField('single_select', {
      options: [
        {
          id: 'option-a',
          field_def_id: 'field-a',
          name: 'Alpha',
          color: null,
          position: 10,
          is_active: true,
          created_at: '',
          updated_at: '',
        },
        {
          id: 'option-b',
          field_def_id: 'field-a',
          name: 'Beta',
          color: null,
          position: 20,
          is_active: true,
          created_at: '',
          updated_at: '',
        },
      ],
    });
    const groups: BoardGroup[] = [
      { key: 'option-b', label: 'Beta', count: 0, wip: null, data: [] },
      { key: 'option-a', label: 'Alpha', count: 0, wip: null, data: [] },
      { key: NONE_KEY, label: 'No Teams', count: 0, wip: null, data: [] },
    ];
    const positionFrame = frame('custom_field_option.updated', {
      field_def_id: 'field-a',
      change: 'updated',
      option: { id: 'option-b', name: 'Beta', position: 5, is_active: true },
    });

    const ordered = applyCustomFieldDefinitionToGroups(groups, positionFrame, 'field-a', [field]);
    expect(ordered.map((group) => group.key)).toEqual(['option-b', 'option-a', NONE_KEY]);

    const columns = groups.map(({ key, label, count, wip }) => ({ key, label, count, wip }));
    const laneTemplates: BoardLane[] = [
      {
        key: 'option-b',
        label: 'Beta',
        count: 0,
        groups: columns.map((column) => ({ key: column.key, count: 0, data: [] })),
      },
      {
        key: 'option-a',
        label: 'Alpha',
        count: 0,
        groups: columns.map((column) => ({ key: column.key, count: 0, data: [] })),
      },
      {
        key: NONE_KEY,
        label: 'No Teams',
        count: 0,
        groups: columns.map((column) => ({ key: column.key, count: 0, data: [] })),
      },
    ];
    const laneOrdered = applyCustomFieldDefinitionToLanes(
      columns,
      laneTemplates,
      positionFrame,
      {
        groupBy: 'field-a',
        subGroupBy: 'field-a',
        customFields: [field],
        belongs: () => true,
      },
    );
    expect(laneOrdered.columns.map((column) => column.key)).toEqual([
      'option-b',
      'option-a',
      NONE_KEY,
    ]);
    expect(laneOrdered.lanes.map((lane) => lane.key)).toEqual([
      'option-b',
      'option-a',
      NONE_KEY,
    ]);
  });

  it('custom field rename 更新 none 标签，双轴/单轴删除保持 skeleton 一致', () => {
    const item = card({
      custom_field_values: [{ field_def_id: 'field-a', value_json: 'option-a' }],
    });
    const base = singleCell(item, NONE_KEY, NONE_KEY);
    const bothContext = {
      groupBy: 'field-a',
      subGroupBy: 'field-a',
      customFields: [customField('single_select')],
      belongs: () => true,
    };
    const renamed = applyCustomFieldDefinitionToLanes(
      base.columns,
      base.lanes,
      frame('custom_field.updated', { id: 'field-a', name: 'Discipline' }),
      bothContext,
    );
    expect(renamed.columns[0]?.label).toBe('No Discipline');
    expect(renamed.lanes[0]?.label).toBe('No Discipline');

    const removedBoth = applyCustomFieldDefinitionToLanes(
      renamed.columns,
      renamed.lanes,
      frame('custom_field.updated', { id: 'field-a', change: 'deleted' }),
      bothContext,
    );
    expect(removedBoth).toEqual({ columns: [], lanes: [] });

    const removedColumn = applyCustomFieldDefinitionToLanes(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('custom_field.updated', { id: 'field-a', is_active: false }),
      { ...LANE_CTX, groupBy: 'field-a' },
    );
    expect(removedColumn.columns).toEqual([]);
    expect(removedColumn.lanes.every((lane) => lane.count === 0 && lane.groups.length === 0)).toBe(
      true,
    );

    const removedLane = applyCustomFieldDefinitionToLanes(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('custom_field.updated', { id: 'field-a', change: 'deleted' }),
      { ...LANE_CTX, subGroupBy: 'field-a' },
    );
    expect(removedLane.columns.every((column) => column.count === 0)).toBe(true);
    expect(removedLane.lanes).toEqual([]);
  });

  it('不相关或不完整 custom definition 帧不扰动二维投影', () => {
    const baseLanes = lanes();
    for (const event of [
      frame('custom_field.updated', { id: 'other', name: 'Other' }),
      frame('custom_field_option.updated', { field_def_id: 'other', id: 'option-a' }),
      frame('custom_field_option.updated', { field_def_id: 'field-a', option: { name: 'No id' } }),
      frame('custom_field_option.updated', { field_def_id: 'field-a', id: 'option-a' }),
    ]) {
      const result = applyCustomFieldDefinitionToLanes(SWIMLANE_COLUMNS, baseLanes, event, {
        ...LANE_CTX,
        groupBy: 'field-a',
      });
      expect(result.columns).toBe(SWIMLANE_COLUMNS);
      expect(result.lanes).toBe(baseLanes);
    }
  });
});

describe('applyBoardFrame 单卡增量(§3.5)', () => {
  it('同列非排序字段更新保持卡片顺序，显式 sort 对 created/updated 重新定位', () => {
    const a = card({ id: 'a', identifier: 'WEB-1', position: 1, updated_at: '2026-07-26T10:00:00Z' });
    const b = card({ id: 'b', identifier: 'WEB-2', position: 2, updated_at: '2026-07-26T10:01:00Z' });
    const c = card({ id: 'c', identifier: 'WEB-3', position: 3, updated_at: '2026-07-26T10:02:00Z' });
    const base: BoardGroup[] = [
      { key: 'todo', label: 'Todo', count: 3, wip: null, data: [a, b, c] },
    ];

    const updated = applyBoardFrame(
      base,
      frame('issue.updated', {
        id: 'b',
        changes: { title: 'Updated title' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      CTX,
    );
    expect(updated.groups[0]?.data.map((item) => item.id)).toEqual(['a', 'b', 'c']);

    const sorted = applyBoardFrame(
      base,
      frame('issue.created', {
        issue: card({
          id: 'newest',
          identifier: 'WEB-4',
          position: 99,
          updated_at: '2026-07-26T12:00:00Z',
        }),
      }),
      { ...CTX, sort: [{ field: 'updated_at', order: 'desc' }] },
    );
    expect(sorted.groups[0]?.data.map((item) => item.id)).toEqual(['newest', 'c', 'b', 'a']);
  });

  it('为首次出现的内置轴值使用卡片显示快照，并格式化 none/boolean 标签', () => {
    const empty: BoardGroup[] = [];
    const builtins = [
      {
        axis: 'status',
        expected: 'In review',
        item: card({
          status_id: 'status-review',
          status: { id: 'status-review', name: 'In review', category: 'in_review' },
        }),
      },
      {
        axis: 'assignee',
        expected: 'Ada',
        item: card({ assignee_id: 'member-ada', assignee: { id: 'member-ada', name: 'Ada' } }),
      },
      {
        axis: 'project',
        expected: 'Platform',
        item: card({
          project_id: 'project-platform',
          project: { id: 'project-platform', name: 'Platform', key: 'PLAT' },
        }),
      },
    ] as const;
    for (const sample of builtins) {
      const result = applyBoardFrame(
        empty,
        frame('issue.created', { issue: sample.item }),
        { groupBy: sample.axis, belongs: () => true },
      );
      expect(result.groups[0]?.label).toBe(sample.expected);
    }

    const none = applyBoardFrame(
      empty,
      frame('issue.created', { issue: card({ assignee_id: null, assignee: null }) }),
      { groupBy: 'assignee', belongs: () => true },
    );
    expect(none.groups[0]?.label).toBe('No assignee');

    const booleanField = customField('boolean', { id: 'field-boolean', name: 'Approved' });
    const boolean = applyBoardFrame(
      empty,
      frame('issue.created', {
        issue: card({
          custom_field_values: [
            { field_def_id: booleanField.id, value_boolean: false },
          ],
        }),
      }),
      { groupBy: booleanField.id, customFields: [booleanField], belongs: () => true },
    );
    expect(boolean.groups[0]?.label).toBe('False');
  });

  it('未知 custom member 显示名时请求权威投影，不泄露成员 UUID', () => {
    const memberField = customField('member', { id: 'field-member', name: 'Reviewer' });
    const result = applyBoardFrame(
      [],
      frame('issue.created', {
        issue: card({
          custom_field_values: [
            { field_def_id: memberField.id, value_member_id: 'member-private-uuid' },
          ],
        }),
      }),
      { groupBy: memberField.id, customFields: [memberField], belongs: () => true },
    );

    expect(result.refetch).toBe(true);
    expect(result.groups).toEqual([]);
  });

  it('issue.moved 跨列重分桶并调整计数', () => {
    const result = applyBoardFrame(
      groups(),
      frame('issue.moved', {
        id: 'i1',
        changes: { state_category: 'in_progress', status_id: 'st_ip' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      CTX,
    );
    expect(result.refetch).toBe(false);
    const todo = result.groups.find((g) => g.key === 'todo');
    const ip = result.groups.find((g) => g.key === 'in_progress');
    expect(todo?.data).toHaveLength(0);
    expect(todo?.count).toBe(0);
    expect(ip?.data).toHaveLength(1);
    expect(ip?.count).toBe(1);
    expect(ip?.data[0]?.state_category).toBe('in_progress');
  });

  it('issue.updated 就地合并字段', () => {
    const result = applyBoardFrame(
      groups(),
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'urgent' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      CTX,
    );
    const todo = result.groups.find((g) => g.key === 'todo');
    expect(todo?.data[0]?.priority).toBe('urgent');
    expect(todo?.count).toBe(1);
  });

  it('issue.labels_changed immediately updates the existing card label cache', () => {
    const labels = [{ id: 'lbl-bug', name: 'bug', color: '#e5484d' }];
    const result = applyBoardFrame(
      groups(),
      frame('issue.labels_changed', { issue_id: 'i1', labels }),
      CTX,
    );
    expect(result.groups.find((group) => group.key === 'todo')?.data[0]?.labels).toEqual(labels);
    expect(result.refetch).toBe(false);
  });

  it('不再 belongs → 从视图移除', () => {
    const result = applyBoardFrame(
      groups(),
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'low' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      { groupBy: 'state_category', belongs: (c) => c.priority === 'high' },
    );
    expect(result.groups.find((g) => g.key === 'todo')?.data).toHaveLength(0);
  });

  it('issue.deleted 移除卡片', () => {
    const result = applyBoardFrame(groups(), frame('issue.deleted', { id: 'i1' }), CTX);
    expect(result.groups.find((g) => g.key === 'todo')?.data).toHaveLength(0);
  });

  it('issue.created 插入对应列', () => {
    const created = card({ id: 'i2', identifier: 'WEB-2', state_category: 'in_progress' });
    const result = applyBoardFrame(groups(), frame('issue.created', { issue: created }), CTX);
    const ip = result.groups.find((g) => g.key === 'in_progress');
    expect(ip?.data.map((c) => c.id)).toContain('i2');
    expect(ip?.count).toBe(1);
  });

  it('created 但已存在或不属于视图 → 不变', () => {
    const base = groups();
    const dup = card({ id: 'i1' });
    const r1 = applyBoardFrame(base, frame('issue.created', { issue: dup }), CTX);
    expect(r1.groups).toBe(base); // 原引用返回(无变化)
    const foreign = card({ id: 'i3', state_category: 'todo' });
    const r2 = applyBoardFrame(base, frame('issue.created', { issue: foreign }), {
      groupBy: 'state_category',
      belongs: () => false,
    });
    expect(r2.groups).toBe(base);
    expect(r2.groups.find((g) => g.key === 'todo')?.data).toHaveLength(1);
  });

  it('防回退:旧 updated_at 丢弃', () => {
    const result = applyBoardFrame(
      groups(),
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'low' },
        updated_at: '2026-07-26T09:00:00Z',
      }),
      CTX,
    );
    expect(result.groups.find((g) => g.key === 'todo')?.data[0]?.priority).toBe('high');
  });

  it('view.updated → refetch;view.presence 不动', () => {
    const base = groups();
    const updated = applyBoardFrame(base, frame('view.updated', { id: 'v1' }), CTX);
    expect(updated.refetch).toBe(true);
    const presence = applyBoardFrame(
      base,
      frame('view.presence', { view_id: 'v1', online: 2 }),
      CTX,
    );
    expect(presence.refetch).toBe(false);
    expect(presence.groups).toBe(base);
  });

  it('无关帧返回原引用', () => {
    const base = groups();
    const result = applyBoardFrame(base, frame('comment.created', { id: 'c1' }), CTX);
    expect(result.groups).toBe(base);
  });

  it('缺失载荷保持稳定，无时间戳更新与新分组仍可增量收敛', () => {
    const base = groups();
    for (const invalid of [
      frame('issue.created', { issue: null }),
      frame('issue.created', { issue: 'bad' }),
      frame('issue.created', { issue: { id: 42 } }),
      frame('issue.updated', {}),
      frame('issue.updated', { id: 'missing', changes: { priority: 'low' } }),
    ]) {
      expect(applyBoardFrame(base, invalid, CTX).groups).toBe(base);
    }

    const updated = applyBoardFrame(
      base,
      frame('issue.updated', { id: 'i1', changes: { title: 'No timestamp' } }),
      CTX,
    );
    expect(updated.groups[0]?.data[0]?.title).toBe('No timestamp');

    const dynamic = applyBoardFrame(
      base,
      frame('issue.created', {
        issue: card({ id: 'i2', identifier: 'WEB-2', state_category: 'triage' }),
      }),
      CTX,
    );
    expect(dynamic.groups.at(-1)).toMatchObject({ key: 'triage', label: 'triage', count: 1 });
  });

  it.each(['updated', 'moved', 'project_changed'])(
    'issue.%s 目标卡不在投影内 → 请求该 id 单卡对账',
    (action) => {
      const base = groups();
      const result = applyBoardFrame(base, frame(`issue.${action}`, { id: 'i-enter' }), CTX);
      expect(result.groups).toBe(base);
      expect(result.refetch).toBe(false);
      expect(result.reconcileIssueId).toBe('i-enter');
    },
  );

  it('缺失 deleted/未知动作不请求单卡对账', () => {
    const base = groups();
    expect(
      applyBoardFrame(base, frame('issue.deleted', { id: 'i-missing' }), CTX).reconcileIssueId,
    ).toBeUndefined();
    expect(
      applyBoardFrame(base, frame('issue.archived', { id: 'i-missing' }), CTX).reconcileIssueId,
    ).toBeUndefined();
  });
});

describe('applyBoardLaneFrame 二维 cell 增量(§3.5)', () => {
  it('同 cell 更新保持位置，并按视图 sort 插入新卡', () => {
    const a = card({ id: 'a', identifier: 'WEB-1', position: 1, updated_at: '2026-07-26T10:00:00Z' });
    const b = card({ id: 'b', identifier: 'WEB-2', position: 2, updated_at: '2026-07-26T10:01:00Z' });
    const c = card({ id: 'c', identifier: 'WEB-3', position: 3, updated_at: '2026-07-26T10:02:00Z' });
    const baseColumns: BoardProjectionColumn[] = [
      { key: 'todo', label: 'Todo', count: 3, wip: null },
    ];
    const baseLanes: BoardLane[] = [
      {
        key: 'high',
        label: 'High',
        count: 3,
        groups: [{ key: 'todo', count: 3, data: [a, b, c] }],
      },
    ];

    const updated = applyBoardLaneFrame(
      baseColumns,
      baseLanes,
      frame('issue.updated', {
        id: 'b',
        changes: { title: 'Updated title' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      LANE_CTX,
    );
    expect(updated.lanes[0]?.groups[0]?.data.map((item) => item.id)).toEqual(['a', 'b', 'c']);

    const sorted = applyBoardLaneFrame(
      baseColumns,
      baseLanes,
      frame('issue.created', {
        issue: card({
          id: 'newest',
          identifier: 'WEB-4',
          position: 99,
          updated_at: '2026-07-26T12:00:00Z',
        }),
      }),
      { ...LANE_CTX, sort: [{ field: 'updated_at', order: 'desc' }] },
    );
    expect(sorted.lanes[0]?.groups[0]?.data.map((item) => item.id)).toEqual([
      'newest',
      'c',
      'b',
      'a',
    ]);
  });

  it('issue.created 插入对应 cell 并同步 lane/column/cell count', () => {
    const created = card({
      id: 'i2',
      identifier: 'WEB-2',
      state_category: 'done',
      priority: 'low',
    });
    const result = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.created', { issue: created }),
      LANE_CTX,
    );

    expect(result.refetch).toBe(false);
    expect(result.columns.find((column) => column.key === 'done')?.count).toBe(1);
    const low = result.lanes.find((lane) => lane.key === 'low');
    expect(low?.count).toBe(1);
    expect(low?.groups.find((group) => group.key === 'done')).toMatchObject({
      count: 1,
      data: [expect.objectContaining({ id: 'i2' })],
    });
  });

  it('issue.updated 按更新后的两轴重分 cell，不改主列总数', () => {
    const result = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'low', title: 'Updated' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      LANE_CTX,
    );

    expect(result.columns.find((column) => column.key === 'todo')?.count).toBe(1);
    expect(result.lanes.find((lane) => lane.key === 'high')?.count).toBe(0);
    const moved = result.lanes
      .find((lane) => lane.key === 'low')
      ?.groups.find((group) => group.key === 'todo')?.data[0];
    expect(moved).toMatchObject({ id: 'i1', priority: 'low', title: 'Updated' });
  });

  it('issue.moved 优先采用 payload.to.group_key/to_sub_group 并更新位置', () => {
    const result = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.moved', {
        id: 'i1',
        to: { group_key: 'done' },
        to_sub_group: 'low',
        position: 8,
        updated_at: '2026-07-26T11:00:00Z',
      }),
      LANE_CTX,
    );

    expect(result.columns.map((column) => [column.key, column.count])).toEqual([
      ['todo', 0],
      ['done', 1],
    ]);
    const moved = result.lanes
      .find((lane) => lane.key === 'low')
      ?.groups.find((group) => group.key === 'done')?.data[0];
    expect(moved).toMatchObject({
      id: 'i1',
      state_category: 'done',
      priority: 'low',
      position: 8,
    });
  });

  it('issue.deleted 从 cell 移除并递减三层 count', () => {
    const result = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.deleted', { id: 'i1' }),
      LANE_CTX,
    );
    expect(result.columns.find((column) => column.key === 'todo')?.count).toBe(0);
    expect(result.lanes.find((lane) => lane.key === 'high')?.count).toBe(0);
    expect(
      result.lanes
        .find((lane) => lane.key === 'high')
        ?.groups.find((group) => group.key === 'todo'),
    ).toMatchObject({ count: 0, data: [] });
  });

  it('issue.project_changed 用 to_project_id 与 status mapping 更新项目泳道', () => {
    const projectColumns: readonly BoardProjectionColumn[] = [
      { key: 'todo', label: 'Todo', count: 1, wip: null },
    ];
    const projectLanes: readonly BoardLane[] = [
      {
        key: '__none__',
        label: 'No project',
        count: 1,
        groups: [{ key: 'todo', count: 1, data: [card()] }],
      },
    ];
    const result = applyBoardLaneFrame(
      projectColumns,
      projectLanes,
      frame('issue.project_changed', {
        id: 'i1',
        to_project_id: 'p2',
        mapped_fields: [
          {
            field: 'status',
            to: { id: 'st2', name: 'Todo 2', category: 'todo' },
          },
        ],
        updated_at: '2026-07-26T11:00:00Z',
      }),
      { groupBy: 'state_category', subGroupBy: 'project', belongs: () => true },
    );
    const target = result.lanes.find((lane) => lane.key === 'p2');
    expect(target?.groups[0]?.data[0]).toMatchObject({
      project_id: 'p2',
      status_id: 'st2',
      state_category: 'todo',
    });
  });

  it('过期/重复/不属于视图的帧保持稳定，只有 view.updated 请求 refetch', () => {
    const baseLanes = lanes();
    const stale = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      baseLanes,
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'low' },
        updated_at: '2026-07-26T09:00:00Z',
      }),
      LANE_CTX,
    );
    expect(stale.columns).toBe(SWIMLANE_COLUMNS);
    expect(stale.lanes).toBe(baseLanes);

    const removed = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      baseLanes,
      frame('issue.updated', {
        id: 'i1',
        changes: { priority: 'low' },
        updated_at: '2026-07-26T11:00:00Z',
      }),
      { ...LANE_CTX, belongs: () => false },
    );
    expect(removed.columns[0]?.count).toBe(0);

    expect(
      applyBoardLaneFrame(SWIMLANE_COLUMNS, baseLanes, frame('view.updated', {}), LANE_CTX).refetch,
    ).toBe(true);
    expect(
      applyBoardLaneFrame(SWIMLANE_COLUMNS, baseLanes, frame('view.presence', {}), LANE_CTX)
        .refetch,
    ).toBe(false);
  });

  it('异常/重复二维帧保持原引用，未知 issue 动作不触发 refetch', () => {
    const baseLanes = lanes();
    const invalidFrames = [
      frame('comment.created', { id: 'c1' }),
      frame('issue.created', { issue: null }),
      frame('issue.created', { issue: 'bad' }),
      frame('issue.created', { issue: { id: 42 } }),
      frame('issue.created', { issue: card() }),
      frame('issue.updated', {}),
      frame('issue.updated', { id: 'missing' }),
      frame('issue.archived', { id: 'i1' }),
    ];
    for (const invalid of invalidFrames) {
      const result = applyBoardLaneFrame(SWIMLANE_COLUMNS, baseLanes, invalid, LANE_CTX);
      expect(result.columns).toBe(SWIMLANE_COLUMNS);
      expect(result.lanes).toBe(baseLanes);
      expect(result.refetch).toBe(false);
    }

    const foreign = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      baseLanes,
      frame('issue.created', {
        issue: card({ id: 'i2', identifier: 'WEB-2' }),
      }),
      { ...LANE_CTX, belongs: () => false },
    );
    expect(foreign.lanes).toBe(baseLanes);
  });

  it('moved 支持轴字段回退、动态 status/assignee/project cell 与空值轴', () => {
    const base = singleCell(card(), 'todo', 'high');
    const axisFallback = applyBoardLaneFrame(
      base.columns,
      base.lanes,
      frame('issue.moved', {
        id: 'i1',
        to: { state_category: 'done', priority: 'low' },
      }),
      LANE_CTX,
    );
    expect(
      axisFallback.lanes
        .find((lane) => lane.key === 'low')
        ?.groups.find((group) => group.key === 'done')?.data[0],
    ).toMatchObject({ state_category: 'done', priority: 'low' });

    const statusAssignee = singleCell(card(), 'st_todo', '__none__');
    const assigned = applyBoardLaneFrame(
      statusAssignee.columns,
      statusAssignee.lanes,
      frame('issue.moved', {
        id: 'i1',
        to: { group_key: 'st_done' },
        to_sub_group: 'm2',
      }),
      { groupBy: 'status', subGroupBy: 'assignee', belongs: () => true },
    );
    expect(assigned.columns.find((column) => column.key === 'st_done')?.count).toBe(1);
    expect(assigned.lanes.find((lane) => lane.key === 'm2')?.groups[1]?.data[0]).toMatchObject({
      status_id: 'st_done',
      assignee_id: 'm2',
    });

    const projectState = singleCell(
      card({ project_id: 'p1', state_category: 'todo' }),
      'p1',
      'todo',
    );
    const cleared = applyBoardLaneFrame(
      projectState.columns,
      projectState.lanes,
      frame('issue.moved', {
        id: 'i1',
        to: { group_key: '__none__' },
        to_sub_group: 'done',
      }),
      { groupBy: 'project', subGroupBy: 'state_category', belongs: () => true },
    );
    expect(
      cleared.lanes
        .find((lane) => lane.key === 'done')
        ?.groups.find((group) => group.key === '__none__')?.data[0],
    ).toMatchObject({ project_id: null, state_category: 'done' });

    const changesOnly = applyBoardLaneFrame(
      base.columns,
      base.lanes,
      frame('issue.moved', { id: 'i1', changes: { priority: 'low' }, position: 'bad' }),
      LANE_CTX,
    );
    expect(changesOnly.lanes.find((lane) => lane.key === 'low')?.count).toBe(1);
  });

  it('project_changed 对 inbox、缺省 mapping 与精简 status mapping 均 fail closed', () => {
    const project = singleCell(card({ project_id: 'p1' }), 'todo', 'p1');
    const context = {
      groupBy: 'state_category',
      subGroupBy: 'project',
      belongs: () => true,
    };
    const inbox = applyBoardLaneFrame(
      project.columns,
      project.lanes,
      frame('issue.project_changed', { id: 'i1', to_project_id: null }),
      context,
    );
    expect(inbox.lanes.find((lane) => lane.key === '__none__')?.count).toBe(1);

    for (const mapped_fields of [
      [{ field: 'milestone_id', to: null }],
      [{ field: 'status', to: null }],
      [{ field: 'status', to: { name: 'Missing id' } }],
    ]) {
      const result = applyBoardLaneFrame(
        project.columns,
        project.lanes,
        frame('issue.project_changed', { id: 'i1', to_project_id: 'p2', mapped_fields }),
        context,
      );
      expect(result.lanes.find((lane) => lane.key === 'p2')?.count).toBe(1);
    }

    const compact = applyBoardLaneFrame(
      project.columns,
      project.lanes,
      frame('issue.project_changed', {
        id: 'i1',
        to_project_id: 'p2',
        mapped_fields: [{ field: 'status', to: { id: 'st2' } }],
      }),
      context,
    );
    expect(compact.lanes.find((lane) => lane.key === 'p2')?.groups[0]?.data[0]).toMatchObject({
      status_id: 'st2',
      state_category: 'todo',
      status: { id: 'st2', name: 'Todo', category: 'todo' },
    });
  });

  it('重建时保留同 cell 多卡并为新列/泳道补全交叉 cell', () => {
    const sameCell = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.created', {
        issue: card({ id: 'i2', identifier: 'WEB-2' }),
      }),
      LANE_CTX,
    );
    expect(sameCell.lanes[0]?.groups[0]).toMatchObject({ count: 2 });

    const dynamic = applyBoardLaneFrame(
      SWIMLANE_COLUMNS,
      lanes(),
      frame('issue.created', {
        issue: card({
          id: 'i3',
          identifier: 'WEB-3',
          state_category: 'triage',
          priority: 'medium',
        }),
      }),
      LANE_CTX,
    );
    expect(dynamic.columns.at(-1)).toMatchObject({ key: 'triage', count: 1 });
    expect(dynamic.lanes.at(-1)?.groups).toHaveLength(dynamic.columns.length);
  });

  it.each(['updated', 'moved', 'project_changed'])(
    '二维 issue.%s 目标卡不在投影内 → 请求该 id 单卡对账',
    (action) => {
      const baseLanes = lanes();
      const result = applyBoardLaneFrame(
        SWIMLANE_COLUMNS,
        baseLanes,
        frame(`issue.${action}`, { id: 'i-enter' }),
        LANE_CTX,
      );
      expect(result.columns).toBe(SWIMLANE_COLUMNS);
      expect(result.lanes).toBe(baseLanes);
      expect(result.refetch).toBe(false);
      expect(result.reconcileIssueId).toBe('i-enter');
    },
  );

  it('二维缺失 deleted/未知动作不请求单卡对账', () => {
    const baseLanes = lanes();
    expect(
      applyBoardLaneFrame(
        SWIMLANE_COLUMNS,
        baseLanes,
        frame('issue.deleted', { id: 'i-missing' }),
        LANE_CTX,
      ).reconcileIssueId,
    ).toBeUndefined();
    expect(
      applyBoardLaneFrame(
        SWIMLANE_COLUMNS,
        baseLanes,
        frame('issue.archived', { id: 'i-missing' }),
        LANE_CTX,
      ).reconcileIssueId,
    ).toBeUndefined();
  });
});

describe('cardBelongsToView 本地重判(§3.5)', () => {
  it('空 filters → 属于', () => {
    expect(cardBelongsToView(card(), {})).toBe(true);
  });

  it('顶层 AND eq/in 评估', () => {
    const filters = {
      operator: 'AND',
      conditions: [
        { field: 'priority', op: 'in', value: ['high', 'urgent'] },
        { field: 'state_category', op: 'eq', value: 'todo' },
      ],
    };
    expect(cardBelongsToView(card(), filters)).toBe(true);
    expect(cardBelongsToView(card({ priority: 'low' }), filters)).toBe(false);
  });

  it('OR 评估', () => {
    const filters = {
      operator: 'OR',
      conditions: [
        { field: 'priority', op: 'eq', value: 'urgent' },
        { field: 'state_category', op: 'eq', value: 'todo' },
      ],
    };
    expect(cardBelongsToView(card({ priority: 'low' }), filters)).toBe(true);
    expect(cardBelongsToView(card({ priority: 'low', state_category: 'done' }), filters)).toBe(
      false,
    );
  });

  it('嵌套/无法判定 → 保守保留', () => {
    const nested = {
      operator: 'AND',
      conditions: [{ operator: 'OR', conditions: [{ field: 'priority', op: 'eq', value: 'x' }] }],
    };
    expect(cardBelongsToView(card(), nested)).toBe(true);
    const range = { operator: 'AND', conditions: [{ field: 'due_date', op: 'lte', value: 'x' }] };
    expect(cardBelongsToView(card(), range)).toBe(true);
  });

  it('not_in/label 可本地评估，不支持字段与自定义字段仍保守', () => {
    const notIn = {
      operator: 'AND',
      conditions: [{ field: 'priority', op: 'not_in', value: ['low'] }],
    };
    expect(cardBelongsToView(card({ priority: 'high' }), notIn)).toBe(true);
    expect(cardBelongsToView(card({ priority: 'low' }), notIn)).toBe(false);
    const unknown = {
      operator: 'AND',
      conditions: [{ field: 'milestone_id', op: 'eq', value: 'x' }],
    };
    expect(cardBelongsToView(card(), unknown)).toBe(true);
    const label = { operator: 'AND', conditions: [{ field: 'label', op: 'in', value: ['l'] }] };
    expect(cardBelongsToView(card(), label)).toBe(false);
    expect(
      cardBelongsToView(card({ labels: [{ id: 'l', name: 'bug', color: '#e5484d' }] }), label),
    ).toBe(true);
    const custom = {
      operator: 'AND',
      conditions: [{ field_kind: 'custom_field', op: 'eq', value: 'x' }],
    };
    expect(cardBelongsToView(card(), custom)).toBe(true);
    const nonObject = { operator: 'AND', conditions: ['oops'] };
    expect(cardBelongsToView(card(), nonObject)).toBe(true);
  });
});

describe('rebucketGroups(按 group_by 本地重分桶,§4.2)', () => {
  const g = (key: string, data: BoardCard[], label = key): BoardGroup => ({
    key,
    label,
    count: data.length,
    wip: null,
    data,
  });

  it('按新 group_by 重分桶并保留组标签', () => {
    const a = card({ id: 'a', priority: 'high', state_category: 'todo' });
    const b = card({ id: 'b', priority: 'high', state_category: 'done' });
    const c = card({ id: 'c', priority: 'low', state_category: 'todo' });
    const groups = [g('todo', [a, c], 'Todo'), g('done', [b], 'Done')];
    const byPriority = rebucketGroups(groups, 'priority');
    const high = byPriority.find((x) => x.key === 'high');
    expect(high?.data.map((x) => x.id).sort()).toEqual(['a', 'b']);
    expect(high?.count).toBe(2);
    expect(byPriority.find((x) => x.key === 'low')?.data.map((x) => x.id)).toEqual(['c']);
  });

  it('assignee/project 无值 → __none__ 列与回退标签', () => {
    const a = card({ id: 'a', assignee_id: null });
    const groups = [g('todo', [a], 'Todo')];
    const byAssignee = rebucketGroups(groups, 'assignee');
    const none = byAssignee.find((x) => x.key === NONE_KEY);
    expect(none?.label).toBe('No assignee');
    const byProject = rebucketGroups(groups, 'project');
    expect(byProject.find((x) => x.key === NONE_KEY)?.label).toBe('No project');
  });

  it('assignee/project 动态目标轴使用卡片内嵌实体名称', () => {
    const item = card({
      assignee_id: 'member-1',
      assignee: { id: 'member-1', name: 'Ada' },
      project_id: 'project-1',
      project: { id: 'project-1', name: 'Platform', key: 'PLAT' },
    });
    const source = [g('todo', [item], 'Todo')];

    expect(rebucketGroups(source, 'assignee')[0]?.label).toBe('Ada');
    expect(rebucketGroups(source, 'project')[0]?.label).toBe('Platform');
  });

  it('status 分组回退到卡片状态名', () => {
    const a = card({
      id: 'a',
      status: { id: 'st1', name: 'In Review', category: 'in_review' },
      status_id: 'st1',
    });
    const groups = [g('todo', [a], 'Todo')];
    const byStatus = rebucketGroups(groups, 'status');
    expect(byStatus.find((x) => x.key === 'st1')?.label).toBe('In Review');
  });

  it('从 label 多值轴切换到单值轴时同一 issue 只重分桶一次', () => {
    const labelled = card({
      id: 'multi',
      labels: [
        { id: 'label-a', name: 'Alpha', color: '#111111' },
        { id: 'label-b', name: 'Beta', color: '#222222' },
      ],
    });
    const labelGroups = [g('label-a', [labelled], 'Alpha'), g('label-b', [labelled], 'Beta')];

    const byCategory = rebucketGroups(labelGroups, 'state_category');

    expect(byCategory).toHaveLength(1);
    expect(byCategory[0]?.data.map((item) => item.id)).toEqual(['multi']);
    expect(byCategory[0]?.count).toBe(1);
  });

  it('预览 label 与 multi_select 目标轴时按完整多值集合分桶', () => {
    const field = customField('multi_select', {
      id: 'field-teams',
      name: 'Teams',
      options: [
        {
          id: 'option-a',
          field_def_id: 'field-teams',
          name: 'Alpha',
          color: null,
          position: 0,
          is_active: true,
          created_at: '',
          updated_at: '',
        },
        {
          id: 'option-b',
          field_def_id: 'field-teams',
          name: 'Beta',
          color: null,
          position: 1,
          is_active: true,
          created_at: '',
          updated_at: '',
        },
      ],
    });
    const item = card({
      labels: [
        { id: 'label-b', name: 'Beta', color: '#222222' },
        { id: 'label-a', name: 'Alpha', color: '#111111' },
      ],
      custom_field_values: [{ field_def_id: field.id, value_json: ['option-a', 'option-b'] }],
    });
    const source = [g('todo', [item], 'Todo')];

    expect(rebucketGroups(source, 'label').map((group) => [group.key, group.label])).toEqual([
      ['label-a', 'Alpha'],
      ['label-b', 'Beta'],
    ]);
    expect(
      rebucketGroups(source, field.id, [field]).map((group) => [group.key, group.label]),
    ).toEqual([
      ['option-a', 'Alpha'],
      ['option-b', 'Beta'],
    ]);
  });

  it('目标轴 __none__ 不继承源轴的标签或 WIP', () => {
    const item = card({ id: 'empty', assignee_id: null, labels: [], custom_field_values: [] });
    const source: BoardGroup[] = [
      {
        key: NONE_KEY,
        label: 'No assignee',
        count: 1,
        wip: { limit: 1, enforcement: 'block' },
        data: [item],
      },
    ];
    const field = customField('multi_select', {
      id: 'field-teams',
      name: 'Teams',
      options: [],
    });

    expect(rebucketGroups(source, 'label')[0]).toMatchObject({
      key: NONE_KEY,
      label: 'No label',
      wip: null,
    });
    expect(rebucketGroups(source, field.id, [field])[0]).toMatchObject({
      key: NONE_KEY,
      label: 'No Teams',
      wip: null,
    });
  });
});
