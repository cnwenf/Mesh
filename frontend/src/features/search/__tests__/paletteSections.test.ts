/**
 * 面板分区组装单测(§4.2.1 空态唯一数据流 / §4.6 分组)。
 */
import { describe, expect, it } from 'vitest';
import type { ShortcutCommand } from '../../../shortcuts/registry';
import {
  buildEmptyQueryRows,
  buildQueryRows,
  entityRowKey,
  filterCommands,
  groupEntityResults,
} from '../paletteSections';
import type { FavoriteEntry, SearchResultItem } from '../types';

function command(id: string, label: string, keywords?: string[]): ShortcutCommand {
  return { id, label, group: 'global', keywords, run: () => undefined };
}

function issueItem(id: string, title: string): SearchResultItem {
  return {
    type: 'issue',
    id,
    title,
    context: { identifier: `K-${id}`, project: null, status: { id: 's', name: 'Todo', category: 'todo' } },
    icon: 'issue',
    url: `/w/acme/issues/${id}`,
  };
}

function memberItem(id: string, title: string): SearchResultItem {
  return {
    type: 'member',
    id,
    title,
    context: { member_type: 'human', role: 'member' },
    icon: 'member',
    url: `/w/acme/members/${id}`,
  };
}

function favorite(id: string, targetType: 'issue' | 'project', targetId: string, createdAt: string): FavoriteEntry {
  return {
    id,
    workspace_id: 'ws-1',
    member_id: 'm-1',
    target_type: targetType,
    target_id: targetId,
    created_at: createdAt,
  };
}

describe('buildEmptyQueryRows(空态唯一数据流,§4.2.1)', () => {
  it('favorites 按 created_at 倒序排在前区', () => {
    const rows = buildEmptyQueryRows({
      favorites: [
        favorite('f1', 'issue', 'i-1', '2026-07-01T00:00:00.000Z'),
        favorite('f2', 'issue', 'i-2', '2026-07-03T00:00:00.000Z'),
        favorite('f3', 'issue', 'i-3', '2026-07-02T00:00:00.000Z'),
      ],
      recents: [],
      commands: [],
      counts: {},
    });
    expect(rows.map((row) => row.kind)).toEqual(['favorite', 'favorite', 'favorite']);
    const keys = rows.map((row) => (row.kind === 'favorite' ? row.favorite.id : ''));
    expect(keys).toEqual(['f2', 'f3', 'f1']);
  });

  it('recents 与 favorites 同 target 去重(不重复展示)', () => {
    const rows = buildEmptyQueryRows({
      favorites: [favorite('f1', 'issue', 'i-1', '2026-07-01T00:00:00.000Z')],
      recents: [
        { type: 'issue', id: 'i-1', title: 'Dup', url: '/u1', at: '2026-07-02T00:00:00.000Z' },
        { type: 'issue', id: 'i-2', title: 'Fresh', url: '/u2', at: '2026-07-02T00:00:00.000Z' },
      ],
      commands: [],
      counts: {},
    });
    const kinds = rows.map((row) => row.kind);
    expect(kinds).toEqual(['favorite', 'recent']);
    // 收藏行经本地 recents 解析出标题/深链
    const favoriteRow = rows[0];
    if (favoriteRow?.kind !== 'favorite') throw new Error('expected favorite row');
    expect(favoriteRow.title).toBe('Dup');
    expect(favoriteRow.url).toBe('/u1');
  });

  it('命令区按使用频次倒序,同分保持注册序(稳定)', () => {
    const rows = buildEmptyQueryRows({
      favorites: [],
      recents: [],
      commands: [command('a', 'Alpha'), command('b', 'Beta'), command('c', 'Gamma')],
      counts: { b: 5, a: 5, c: 1 },
    });
    expect(rows.map((row) => (row.kind === 'command' ? row.command.id : ''))).toEqual(['a', 'b', 'c']);
  });

  it('区序固定:favorites → recents → commands', () => {
    const rows = buildEmptyQueryRows({
      favorites: [favorite('f1', 'issue', 'i-9', '2026-07-01T00:00:00.000Z')],
      recents: [{ type: 'project', id: 'p-1', title: 'P', url: '/p', at: '2026-07-01T00:00:00.000Z' }],
      commands: [command('a', 'Alpha')],
      counts: {},
    });
    expect(rows.map((row) => row.kind)).toEqual(['favorite', 'recent', 'command']);
  });

  it('无可解析标题的收藏行以 target_id 兜底且不可跳转(url=null)', () => {
    const rows = buildEmptyQueryRows({
      favorites: [favorite('f1', 'issue', 'i-unknown', '2026-07-01T00:00:00.000Z')],
      recents: [],
      commands: [],
      counts: {},
    });
    const row = rows[0];
    if (row?.kind !== 'favorite') throw new Error('expected favorite row');
    expect(row.title).toBe('i-unknown');
    expect(row.url).toBeNull();
  });
});

describe('filterCommands(本地命令同步过滤,§4.7)', () => {
  const commands = [
    command('new', 'New issue', ['create']),
    command('board', 'Go to board', ['kanban']),
  ];

  it('空查询返回全部', () => {
    expect(filterCommands(commands, '')).toHaveLength(2);
  });

  it('按 label 子串匹配(大小写不敏感)', () => {
    expect(filterCommands(commands, 'BOARD').map((item) => item.id)).toEqual(['board']);
  });

  it('按 keywords 匹配', () => {
    expect(filterCommands(commands, 'kanban').map((item) => item.id)).toEqual(['board']);
    expect(filterCommands(commands, 'create').map((item) => item.id)).toEqual(['new']);
  });

  it('无匹配返回空', () => {
    expect(filterCommands(commands, 'zzz')).toEqual([]);
  });
});

describe('groupEntityResults(按类型分组,规范类型序)', () => {
  it('组间按 issue → member → … 规范序;组内保持服务端序', () => {
    const items = [memberItem('m2', 'Mb'), issueItem('i1', 'Ia'), issueItem('i2', 'Ib'), memberItem('m1', 'Ma')];
    const groups = groupEntityResults(items);
    expect(groups.map((group) => group.type)).toEqual(['issue', 'member']);
    expect(groups[0]?.items.map((item) => item.id)).toEqual(['i1', 'i2']);
    expect(groups[1]?.items.map((item) => item.id)).toEqual(['m2', 'm1']);
  });

  it('空组不出现', () => {
    expect(groupEntityResults([])).toEqual([]);
    expect(groupEntityResults([memberItem('m1', 'M')]).map((group) => group.type)).toEqual(['member']);
  });
});

describe('buildQueryRows(非空 query 行组装)', () => {
  it('命令区在前(零延迟先渲染),实体随后', () => {
    const rows = buildQueryRows([command('c1', 'Cmd')], [issueItem('i1', 'Ia')]);
    expect(rows.map((row) => row.kind)).toEqual(['command', 'entity']);
  });

  it('entityRowKey 稳定(同对象重复计算一致)', () => {
    const item = issueItem('i1', 'Ia');
    expect(entityRowKey(item)).toBe('ent:issue:i1');
    expect(entityRowKey(item)).toBe(entityRowKey({ ...item }));
  });
});
