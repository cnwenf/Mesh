/**
 * recents 本地存储单测(§2.1 三元组隔离 / LRU / 损坏降级 / 惰性清理)。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  RECENTS_MAX,
  pruneRecents,
  readRecents,
  recentTargetKey,
  recordRecent,
  recentsStorageKey,
} from '../recents';

const USER = 'user-1';
const WORKSPACE = 'ws-1';

function item(id: string, type: 'issue' | 'project' = 'issue') {
  return { type, id, title: `Title ${id}`, url: `/w/acme/issues/${id}` } as const;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('recentsStorageKey(三元组隔离,§2.1)', () => {
  it('按 host + user + workspace 三元组隔离', () => {
    const host = window.location.host;
    expect(recentsStorageKey('u1', 'w1')).toBe(`mesh.recents:${host}:u1:w1`);
    expect(recentsStorageKey('u1', 'w1')).not.toBe(recentsStorageKey('u2', 'w1'));
    expect(recentsStorageKey('u1', 'w1')).not.toBe(recentsStorageKey('u1', 'w2'));
  });

  it('不同 user / workspace 的 recents 互不串用', () => {
    recordRecent('u1', 'w1', item('a'));
    expect(readRecents('u2', 'w1')).toHaveLength(0);
    expect(readRecents('u1', 'w2')).toHaveLength(0);
    expect(readRecents('u1', 'w1')).toHaveLength(1);
  });
});

describe('recordRecent / readRecents(LRU,§2.1)', () => {
  it('新条目入队首,读取按访问时间倒序', () => {
    recordRecent(USER, WORKSPACE, item('a'), '2026-07-01T00:00:00.000Z');
    recordRecent(USER, WORKSPACE, item('b'), '2026-07-02T00:00:00.000Z');
    const entries = readRecents(USER, WORKSPACE);
    expect(entries.map((entry) => entry.id)).toEqual(['b', 'a']);
  });

  it('超过上限 20 条时淘汰最旧(LRU)', () => {
    for (let i = 1; i <= RECENTS_MAX + 1; i += 1) {
      recordRecent(USER, WORKSPACE, item(`n${i}`));
    }
    const entries = readRecents(USER, WORKSPACE);
    expect(entries).toHaveLength(RECENTS_MAX);
    expect(entries[0]?.id).toBe(`n${RECENTS_MAX + 1}`);
    expect(entries.map((entry) => entry.id)).not.toContain('n1');
  });

  it('重复访问(同 type + id)提到队首并刷新标题/链接/时间,不新增条目', () => {
    recordRecent(USER, WORKSPACE, item('a'), '2026-07-01T00:00:00.000Z');
    recordRecent(USER, WORKSPACE, item('b'), '2026-07-02T00:00:00.000Z');
    const updated = recordRecent(
      USER,
      WORKSPACE,
      { type: 'issue', id: 'a', title: 'Renamed', url: '/w/acme/issues/a2' },
      '2026-07-03T00:00:00.000Z',
    );
    expect(updated).toHaveLength(2);
    expect(updated[0]).toMatchObject({ id: 'a', title: 'Renamed', url: '/w/acme/issues/a2' });
    expect(updated[0]?.at).toBe('2026-07-03T00:00:00.000Z');
  });

  it('不同 type 同 id 视为不同 target', () => {
    recordRecent(USER, WORKSPACE, item('x', 'issue'));
    recordRecent(USER, WORKSPACE, item('x', 'project'));
    expect(readRecents(USER, WORKSPACE)).toHaveLength(2);
  });
});

describe('损坏/异常降级', () => {
  it('JSON 损坏 → 空列表(不抛错)', () => {
    localStorage.setItem(recentsStorageKey(USER, WORKSPACE), '{corrupted');
    expect(readRecents(USER, WORKSPACE)).toEqual([]);
  });

  it('非数组 JSON → 空列表', () => {
    localStorage.setItem(recentsStorageKey(USER, WORKSPACE), '{"a":1}');
    expect(readRecents(USER, WORKSPACE)).toEqual([]);
  });

  it('非法条目被剔除,合法条目保留', () => {
    const key = recentsStorageKey(USER, WORKSPACE);
    const valid = { type: 'issue', id: 'ok', title: 't', url: '/u', at: '2026-07-01T00:00:00.000Z' };
    localStorage.setItem(
      key,
      JSON.stringify([{ type: 'spaceship', id: 1 }, null, valid, { type: 'issue' }]),
    );
    const entries = readRecents(USER, WORKSPACE);
    expect(entries).toEqual([valid]);
  });

  it('localStorage 读取抛错 → 空列表', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(readRecents(USER, WORKSPACE)).toEqual([]);
  });

  it('localStorage 写入抛错 → 不阻断,返回内存态列表', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    const updated = recordRecent(USER, WORKSPACE, item('a'));
    expect(updated).toHaveLength(1);
  });
});

describe('pruneRecents(惰性失效清理,§4.2.1)', () => {
  it('仅保留 target 在 validIds 内的条目并持久化', () => {
    recordRecent(USER, WORKSPACE, item('a'));
    recordRecent(USER, WORKSPACE, item('b'));
    recordRecent(USER, WORKSPACE, item('c', 'project'));
    const kept = pruneRecents(USER, WORKSPACE, new Set([recentTargetKey('issue', 'a'), recentTargetKey('project', 'c')]));
    expect(kept.map((entry) => entry.id)).toEqual(['c', 'a']);
    // 持久化:再读一致
    expect(readRecents(USER, WORKSPACE).map((entry) => entry.id)).toEqual(['c', 'a']);
  });

  it('validIds 为空 → 全部清理', () => {
    recordRecent(USER, WORKSPACE, item('a'));
    expect(pruneRecents(USER, WORKSPACE, new Set())).toEqual([]);
    expect(readRecents(USER, WORKSPACE)).toEqual([]);
  });
});
