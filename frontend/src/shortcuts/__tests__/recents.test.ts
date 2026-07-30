/**
 * recents — LRU 上限/去重置顶、三元组键隔离、损坏 JSON 守卫、不可变更新、命令计数。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import {
  RECENTS_LIMIT,
  clearRecents,
  commandCountKey,
  commandUseCounts,
  listRecents,
  pushRecent,
  recentIdentity,
  recentsKey,
  removeRecent,
  setRecentsScope,
  stableHost,
  trackCommandUse,
} from '../recents';
import type { RecentEntry } from '../recents';

function entry(id: string, at: number, kind: RecentEntry['kind'] = 'object'): RecentEntry {
  return kind === 'command'
    ? { kind, id, commandId: id, title: `cmd ${id}`, at }
    : { kind, type: 'issue', id, title: `issue ${id}`, url: `/issues/${id}`, at };
}

beforeEach(() => {
  window.localStorage.clear();
  setRecentsScope({ userId: 'u-1', workspaceId: 'ws-1' });
});

describe('键隔离(host + user + workspace 三元组,§2.1)', () => {
  it('recentsKey/commandCountKey 随三元组维度变化', () => {
    expect(recentsKey('h', 'u', 'w')).toBe('mesh.recents:h:u:w');
    expect(recentsKey('h', 'u', 'w2')).not.toBe(recentsKey('h', 'u', 'w'));
    expect(recentsKey('h', 'u2', 'w')).not.toBe(recentsKey('h', 'u', 'w'));
    expect(commandCountKey('h', 'u', 'w')).toBe('mesh.palette.cmdcount:h:u:w');
  });

  it('切换 user/workspace 作用域读不到他区记录(不串用)', () => {
    pushRecent(entry('a', 1));
    setRecentsScope({ userId: 'u-1', workspaceId: 'ws-2' });
    expect(listRecents()).toHaveLength(0);
    setRecentsScope({ userId: 'u-2', workspaceId: 'ws-1' });
    expect(listRecents()).toHaveLength(0);
    setRecentsScope({ userId: 'u-1', workspaceId: 'ws-1' });
    expect(listRecents()).toHaveLength(1);
  });

  it('stableHost 取 API 基址 origin(env 默认 127.0.0.1:8901)', () => {
    expect(stableHost()).toBe('http://127.0.0.1:8901');
  });
});

describe('LRU 语义', () => {
  it('同身份条目去重并置顶(最近访问提前)', () => {
    pushRecent(entry('a', 1));
    pushRecent(entry('b', 2));
    const result = pushRecent(entry('a', 3));
    expect(result.map((item) => item.id)).toEqual(['a', 'b']);
    expect(result[0].at).toBe(3);
    expect(listRecents().map((item) => item.id)).toEqual(['a', 'b']);
  });

  it('超过 RECENTS_LIMIT 淘汰最旧', () => {
    for (let i = 0; i < RECENTS_LIMIT + 5; i += 1) {
      pushRecent(entry(`id-${i}`, i));
    }
    const recents = listRecents();
    expect(recents).toHaveLength(RECENTS_LIMIT);
    expect(recents[0].id).toBe(`id-${RECENTS_LIMIT + 4}`);
    expect(recents.map((item) => item.id)).not.toContain('id-0');
  });

  it('命令条目按 commandId 身份去重(与对象条目空间独立)', () => {
    pushRecent(entry('x', 1, 'command'));
    pushRecent(entry('x', 2)); // 对象 id 'x' 与命令 'x' 不同身份
    expect(listRecents()).toHaveLength(2);
    expect(recentIdentity(entry('x', 1, 'command'))).toBe('command:x');
    expect(recentIdentity(entry('x', 2))).toBe('object:issue:x');
  });

  it('listRecents 按 at 倒序(即便存储序乱序)', () => {
    window.localStorage.setItem(
      recentsKey(stableHost(), 'u-1', 'ws-1'),
      JSON.stringify([entry('old', 1), entry('new', 9), entry('mid', 5)]),
    );
    expect(listRecents().map((item) => item.id)).toEqual(['new', 'mid', 'old']);
  });
});

describe('损坏数据守卫', () => {
  it('非法 JSON / 非数组 / 形状不符条目 → 空列表(不抛错)', () => {
    const key = recentsKey(stableHost(), 'u-1', 'ws-1');
    window.localStorage.setItem(key, '{broken');
    expect(listRecents()).toEqual([]);
    window.localStorage.setItem(key, '{"not":"array"}');
    expect(listRecents()).toEqual([]);
    window.localStorage.setItem(
      key,
      JSON.stringify([{ kind: 'object', id: 'ok', title: 't', at: 1 }, { kind: 'weird' }, 42]),
    );
    expect(listRecents()).toHaveLength(1);
    // 损坏后写入恢复正常
    pushRecent(entry('after', 2));
    expect(listRecents().map((item) => item.id)).toEqual(['after', 'ok']);
  });
});

describe('不可变性', () => {
  it('pushRecent/removeRecent 返回新数组,不改入参与既有返回', () => {
    const first = pushRecent(entry('a', 1));
    const second = pushRecent(entry('b', 2));
    expect(first).not.toBe(second);
    expect(first).toHaveLength(1); // 旧返回不被就地修改
    const removed = removeRecent((item) => item.id === 'a');
    expect(removed.map((item) => item.id)).toEqual(['b']);
    expect(second.map((item) => item.id)).toEqual(['b', 'a']);
  });
});

describe('removeRecent / clearRecents', () => {
  it('removeRecent 按谓词剔除并持久化', () => {
    pushRecent(entry('a', 1));
    pushRecent(entry('b', 2));
    removeRecent((item) => item.id === 'a');
    expect(listRecents().map((item) => item.id)).toEqual(['b']);
  });

  it('clearRecents 仅清当前作用域', () => {
    pushRecent(entry('a', 1));
    setRecentsScope({ userId: 'u-1', workspaceId: 'ws-2' });
    pushRecent(entry('z', 1));
    setRecentsScope({ userId: 'u-1', workspaceId: 'ws-1' });
    clearRecents();
    expect(listRecents()).toHaveLength(0);
    setRecentsScope({ userId: 'u-1', workspaceId: 'ws-2' });
    expect(listRecents()).toHaveLength(1);
  });
});

describe('命令使用计数(常用命令排序依据)', () => {
  it('trackCommandUse 累加;commandUseCounts 解析守卫', () => {
    trackCommandUse('nav.board');
    trackCommandUse('nav.board');
    trackCommandUse('theme.dark');
    expect(commandUseCounts()).toEqual({ 'nav.board': 2, 'theme.dark': 1 });
    // 损坏 → 空;再写恢复
    window.localStorage.setItem(commandCountKey(stableHost(), 'u-1', 'ws-1'), '##');
    expect(commandUseCounts()).toEqual({});
    trackCommandUse('nav.board');
    expect(commandUseCounts()).toEqual({ 'nav.board': 1 });
  });

  it('非法计数值(负数/非数)被过滤', () => {
    window.localStorage.setItem(
      commandCountKey(stableHost(), 'u-1', 'ws-1'),
      JSON.stringify({ a: -1, b: 'x', c: 3 }),
    );
    expect(commandUseCounts()).toEqual({ c: 3 });
  });
});
