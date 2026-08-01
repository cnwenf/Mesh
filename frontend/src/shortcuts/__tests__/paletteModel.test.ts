/**
 * paletteModel — 分组组装(查询态六类序/空态唯一数据流 + 去重 + 频次排序)、
 * 稳定 id、选择移动/异步补入收敛、激活副作用。
 */
import { describe, expect, it, vi } from 'vitest';
import type { PaletteFavorite, SearchItem } from '../../api/search';
import type { ShortcutCommand } from '../registry';
import {
  TOP_COMMANDS_LIMIT,
  activatePaletteOption,
  buildEmptySections,
  buildQuerySections,
  commandStableId,
  entityStableId,
  filterCommands,
  flattenSections,
  iconForSemanticKey,
  moveSelection,
  optionDomId,
  reconcileSelection,
  subtitleForItem,
} from '../paletteModel';
import type { PaletteOption } from '../paletteModel';
import type { RecentEntry } from '../recents';

function issue(id: string, title: string): SearchItem {
  return {
    type: 'issue',
    id,
    title,
    context: {
      identifier: `K-${id}`,
      project: { id: 'p', name: 'Proj' },
      status: { id: 's', name: 'Todo', category: 'todo' },
    },
    icon: 'issue',
    url: `/issues/${id}`,
  };
}

function agent(id: string): SearchItem {
  return {
    type: 'agent',
    id,
    title: `Agent ${id}`,
    context: {
      member_type: 'agent',
      role: 'member',
      capacity: { running: 2, queued: 1, awaiting_approval: 0 },
    },
    icon: 'agent',
    url: `/members/${id}`,
  };
}

function command(id: string, label?: string): ShortcutCommand {
  return { id, label: label ?? `Command ${id}`, group: 'global', run: vi.fn() };
}

describe('稳定 id 与图标映射', () => {
  it('stableId/domId 形态稳定', () => {
    expect(entityStableId(issue('i1', 't'))).toBe('issue:i1');
    expect(commandStableId('nav.board')).toBe('cmd:nav.board');
    expect(optionDomId('issue:i1')).toBe('palette-opt-issue:i1');
  });

  it('语义图标键映射;未知键落 info', () => {
    expect(iconForSemanticKey('agent')).toBe('agent');
    expect(iconForSemanticKey('chat_session')).toBe('chat');
    expect(iconForSemanticKey('???')).toBe('info');
  });
});

describe('filterCommands(本地同步过滤)', () => {
  const commands = [
    { ...command('a', 'Create issue'), keywords: ['new'] },
    command('b', 'Go to board'),
  ];
  it('label 与 keywords 命中;空 query 返回全部', () => {
    expect(filterCommands(commands, '')).toHaveLength(2);
    expect(filterCommands(commands, 'board').map((entry) => entry.id)).toEqual(['b']);
    expect(filterCommands(commands, 'NEW').map((entry) => entry.id)).toEqual(['a']);
    expect(filterCommands(commands, 'zzz')).toHaveLength(0);
  });
});

describe('buildQuerySections(有 query)', () => {
  it('分组序固定 issues → members → … → commands;组内保持服务端序', () => {
    const items: SearchItem[] = [
      {
        type: 'chat_session',
        id: 'c1',
        title: 'Chat',
        context: { participants_count: 2 },
        icon: 'chat_session',
        url: '/chat/c1',
      },
      agent('a1'),
      issue('i1', 'A'),
      issue('i2', 'B'),
    ];
    const sections = buildQuerySections(items, [command('cmd1', 'Create issue')], 'iss');
    expect(sections.map((section) => section.key)).toEqual(['issues', 'members', 'chats', 'commands']);
    expect(sections[0].options.map((option) => option.stableId)).toEqual(['issue:i1', 'issue:i2']);
    expect(sections[0].labelKey).toBe('search.group.issues');
  });

  it('member 与 agent 并入 members 组;空组不出现', () => {
    const member: SearchItem = {
      type: 'member',
      id: 'm1',
      title: 'Human',
      context: { member_type: 'human', role: 'admin' },
      icon: 'member',
      url: '/members/m1',
    };
    const sections = buildQuerySections([member, agent('a1')], [], 'x');
    expect(sections.map((section) => section.key)).toEqual(['members']);
    expect(sections[0].options).toHaveLength(2);
  });
});

describe('subtitleForItem(结构化 context → 目录键 + 参数)', () => {
  it('issue/member(含 agent 容量)/project/view/chat 各取键与参数', () => {
    expect(subtitleForItem(issue('i', 't'))).toEqual({
      key: 'search.subtitle.issue',
      params: { identifier: 'K-i', project: 'Proj', status: 'Todo' },
    });
    expect(subtitleForItem(agent('a'))?.key).toBe('search.subtitle.agent');
    expect(subtitleForItem(agent('a'))?.params).toEqual({
      role: 'member',
      running: 2,
      queued: 1,
      awaiting: 0,
    });
    const project: SearchItem = {
      type: 'project',
      id: 'p',
      title: 'P',
      context: { visibility: 'public', key: 'WEB' },
      icon: 'project',
      url: '/projects/p',
    };
    expect(subtitleForItem(project)).toEqual({
      key: 'search.subtitle.project',
      params: { key: 'WEB', visibility: 'public' },
    });
    const view: SearchItem = {
      type: 'view',
      id: 'v',
      title: 'V',
      context: { scope: 'workspace' },
      icon: 'view',
      url: '/views/v',
    };
    expect(subtitleForItem(view)).toEqual({
      key: 'search.subtitle.view',
      params: { scope: 'workspace' },
    });
  });
});

describe('buildEmptySections(§4.2.1 唯一数据流)', () => {
  const favorites: PaletteFavorite[] = [
    { target_type: 'issue', target_id: 'i-fav', title: 'Fav old', created_at: '2026-01-01T00:00:00Z' },
    { target_type: 'project', target_id: 'p-fav', title: 'Fav new', created_at: '2026-02-01T00:00:00Z' },
  ];
  const recents: RecentEntry[] = [
    { kind: 'object', type: 'issue', id: 'i-fav', title: 'Dup with favorite', at: 9 },
    { kind: 'object', type: 'view', id: 'v-1', title: 'Recent view', url: '/views/v-1', at: 8 },
    { kind: 'command', id: 'nav.board', commandId: 'nav.board', title: 'Board', at: 7 },
    { kind: 'command', id: 'gone.command', commandId: 'gone.command', title: 'Gone', at: 6 },
  ];
  const commands = [command('nav.board', 'Board'), command('nav.home', 'Home'), command('theme.dark', 'Dark')];

  it('favorites(时间倒序)→ recents(去重同 target + 失效命令)→ 常用命令(频次倒序)', () => {
    const sections = buildEmptySections({
      favorites,
      recents,
      commands,
      usageCounts: { 'theme.dark': 5, 'nav.home': 5, 'nav.board': 9 },
    });
    expect(sections.map((section) => section.key)).toEqual(['favorites', 'recents', 'commands']);
    // favorites 按 created_at 倒序
    expect(sections[0].options.map((option) => option.title)).toEqual(['Fav new', 'Fav old']);
    // recents:i-fav 与 favorites 同 target 去重;失效命令剔除;按 at 倒序
    expect(sections[1].options.map((option) => option.stableId)).toEqual(['view:v-1', 'cmd:nav.board']);
    // 常用命令:频次倒序(同频按注册序:nav.home 注册先于 theme.dark),且与 recents 命令去重(nav.board 已在 recents)
    expect(sections[2].options.map((option) => option.stableId)).toEqual([
      'cmd:nav.home',
      'cmd:theme.dark',
    ]);
  });

  it('常用命令上限 TOP_COMMANDS_LIMIT;空输入无分组', () => {
    const many = Array.from({ length: TOP_COMMANDS_LIMIT + 4 }, (_, index) => command(`c${index}`));
    const sections = buildEmptySections({ favorites: [], recents: [], commands: many, usageCounts: {} });
    expect(sections).toHaveLength(1);
    expect(sections[0].options).toHaveLength(TOP_COMMANDS_LIMIT);
    expect(buildEmptySections({ favorites: [], recents: [], commands: [], usageCounts: {} })).toEqual([]);
  });

  it('favorites 缺失 created_at 时保持服务端序;缺失 title 以 target_id 兜底', () => {
    const sections = buildEmptySections({
      favorites: [
        { target_type: 'issue', target_id: 'x1' },
        { target_type: 'issue', target_id: 'x2' },
      ],
      recents: [],
      commands: [],
      usageCounts: {},
    });
    expect(sections[0].options.map((option) => option.title)).toEqual(['x1', 'x2']);
  });
});

describe('选择模型(§4.3.1)', () => {
  const options: PaletteOption[] = [
    { stableId: 'a', group: 'issues', title: 'A', icon: 'info' },
    { stableId: 'b', group: 'issues', title: 'B', icon: 'info' },
    { stableId: 'c', group: 'commands', title: 'C', icon: 'info' },
  ];

  it('moveSelection 循环移动;无当前项时按方向落首/末', () => {
    expect(moveSelection(options, 'a', 1)).toBe('b');
    expect(moveSelection(options, 'c', 1)).toBe('a');
    expect(moveSelection(options, 'a', -1)).toBe('c');
    expect(moveSelection(options, null, 1)).toBe('a');
    expect(moveSelection(options, 'missing', -1)).toBe('c');
    expect(moveSelection([], null, 1)).toBeNull();
  });

  it('reconcileSelection:选中 id 仍在 → 保持;消失 → 索引钳制', () => {
    expect(reconcileSelection(options, 'b', 1)).toEqual({ stableId: 'b', index: 1 });
    // 'x' 消失,原索引 2 → 钳制到新列表末
    const shorter = options.slice(0, 2);
    expect(reconcileSelection(shorter, 'x', 5)).toEqual({ stableId: 'b', index: 1 });
    expect(reconcileSelection([], 'a', 0)).toEqual({ stableId: null, index: -1 });
    // 异步补入:原选 b(索引 1),新列表在 b 前插入 → b 仍选中(索引移动)
    const inserted: PaletteOption[] = [
      { stableId: 'z', group: 'issues', title: 'Z', icon: 'info' },
      ...options,
    ];
    expect(reconcileSelection(inserted, 'b', 1)).toEqual({ stableId: 'b', index: 2 });
  });

  it('flattenSections 跨组保序展平', () => {
    // query 'a' 命中命令 label 'Create'(本地过滤),实体组保持服务端序
    const sections = buildQuerySections([issue('i1', 'A')], [command('cmd1', 'Create')], 'a');
    expect(flattenSections(sections).map((option) => option.stableId)).toEqual([
      'issue:i1',
      'cmd:cmd1',
    ]);
  });
});

describe('activatePaletteOption', () => {
  it('命令:run + 计数 + recent + 收尾', () => {
    const run = vi.fn();
    const recordRecent = vi.fn();
    const recordCommandUse = vi.fn();
    const onAfter = vi.fn();
    const option: PaletteOption = {
      stableId: 'cmd:x',
      group: 'commands',
      title: 'X',
      icon: 'info',
      command: { id: 'x', label: 'X', group: 'global', run },
    };
    activatePaletteOption(
      option,
      { navigate: vi.fn(), openExternal: vi.fn(), recordRecent, recordCommandUse, onAfter },
      { newTab: false },
    );
    expect(run).toHaveBeenCalledTimes(1);
    expect(recordCommandUse).toHaveBeenCalledWith('x');
    expect(recordRecent).toHaveBeenCalledWith(option);
    expect(onAfter).toHaveBeenCalledTimes(1);
  });

  it('实体当前页:newTab=false → navigate + recordRecent', () => {
    const navigate = vi.fn();
    const openExternal = vi.fn();
    const recordRecent = vi.fn();
    const option: PaletteOption = {
      stableId: 'issue:i',
      group: 'issues',
      title: 'T',
      icon: 'info',
      url: '/issues/i',
      item: issue('i', 'T'),
    };
    activatePaletteOption(
      option,
      { navigate, openExternal, recordRecent },
      { newTab: false },
    );
    expect(navigate).toHaveBeenCalledWith('/issues/i');
    expect(recordRecent).toHaveBeenCalled();
    expect(openExternal).not.toHaveBeenCalled();
  });

  it('实体新标签:newTab=true → openExternal,不导航;无 url 静默', () => {
    const navigate = vi.fn();
    const openExternal = vi.fn();
    const option: PaletteOption = {
      stableId: 'issue:i',
      group: 'issues',
      title: 'T',
      icon: 'info',
      url: '/issues/i',
      item: issue('i', 'T'),
    };
    activatePaletteOption(option, { navigate, openExternal }, { newTab: true });
    expect(openExternal).toHaveBeenCalledWith('/issues/i');
    expect(navigate).not.toHaveBeenCalled();
    const noUrl: PaletteOption = { stableId: 'x', group: 'recents', title: 'T', icon: 'info' };
    activatePaletteOption(noUrl, { navigate, openExternal }, { newTab: false });
    expect(navigate).not.toHaveBeenCalled();
  });
});
