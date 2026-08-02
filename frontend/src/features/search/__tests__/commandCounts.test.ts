/**
 * 命令使用计数单测(§4.2.1 常用命令排序依据)。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { commandCountsKey, incrementCommandCount, readCommandCounts } from '../commandCounts';

const USER = 'user-1';

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('readCommandCounts', () => {
  it('键缺失 → 空映射', () => {
    expect(readCommandCounts(USER)).toEqual({});
  });

  it('JSON 损坏 → 空映射', () => {
    localStorage.setItem(commandCountsKey(USER), 'not-json{');
    expect(readCommandCounts(USER)).toEqual({});
  });

  it('非对象或含非法值 → 空映射', () => {
    localStorage.setItem(commandCountsKey(USER), '[1,2]');
    expect(readCommandCounts(USER)).toEqual({});
    localStorage.setItem(commandCountsKey(USER), '{"a":"x"}');
    expect(readCommandCounts(USER)).toEqual({});
    localStorage.setItem(commandCountsKey(USER), '{"a":-1}');
    expect(readCommandCounts(USER)).toEqual({});
    localStorage.setItem(commandCountsKey(USER), '{"a":null}');
    expect(readCommandCounts(USER)).toEqual({});
  });

  it('合法映射原样返回(新对象,不共享引用)', () => {
    localStorage.setItem(commandCountsKey(USER), '{"nav.board":3}');
    const counts = readCommandCounts(USER);
    expect(counts).toEqual({ 'nav.board': 3 });
  });
});

describe('incrementCommandCount', () => {
  it('新命令从 1 计起并持久化', () => {
    const counts = incrementCommandCount(USER, 'nav.board');
    expect(counts).toEqual({ 'nav.board': 1 });
    expect(readCommandCounts(USER)).toEqual({ 'nav.board': 1 });
  });

  it('重复命令累加;不同命令独立', () => {
    incrementCommandCount(USER, 'nav.board');
    incrementCommandCount(USER, 'nav.board');
    const counts = incrementCommandCount(USER, 'theme.light');
    expect(counts).toEqual({ 'nav.board': 2, 'theme.light': 1 });
  });

  it('按用户隔离', () => {
    incrementCommandCount(USER, 'nav.board');
    expect(readCommandCounts('user-2')).toEqual({});
  });

  it('写入抛错 → 不阻断,返回内存态新映射', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    expect(incrementCommandCount(USER, 'nav.board')).toEqual({ 'nav.board': 1 });
  });
});
