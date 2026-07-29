/**
 * 顶栏 ↔ 面板查询桥单测(§4.9)。
 */
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getPaletteQuery,
  setPaletteQuery,
  subscribePaletteQuery,
  takePaletteQuery,
  usePaletteBridgeQuery,
} from '../paletteBridge';

beforeEach(() => {
  // 消费清空,保证用例隔离(模块级状态)
  takePaletteQuery();
});

describe('模块级查询桥', () => {
  it('set / get:设置后可读', () => {
    setPaletteQuery('login');
    expect(getPaletteQuery()).toBe('login');
  });

  it('同值 set 不重复通知', () => {
    setPaletteQuery('x');
    const listener = vi.fn();
    const unsubscribe = subscribePaletteQuery(listener);
    setPaletteQuery('x');
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('take:返回并清空(一次性语义),并通知订阅者', () => {
    setPaletteQuery('login');
    const listener = vi.fn();
    const unsubscribe = subscribePaletteQuery(listener);
    expect(takePaletteQuery()).toBe('login');
    expect(getPaletteQuery()).toBe('');
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it('空值 take 不通知(无状态变化)', () => {
    const listener = vi.fn();
    const unsubscribe = subscribePaletteQuery(listener);
    expect(takePaletteQuery()).toBe('');
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('unsubscribe 后不再收到通知', () => {
    const listener = vi.fn();
    const unsubscribe = subscribePaletteQuery(listener);
    unsubscribe();
    setPaletteQuery('y');
    expect(listener).not.toHaveBeenCalled();
  });
});

describe('usePaletteBridgeQuery(响应式订阅)', () => {
  it('随桥接查询变化重渲染', () => {
    const { result } = renderHook(() => usePaletteBridgeQuery());
    expect(result.current).toBe('');
    act(() => setPaletteQuery('board'));
    expect(result.current).toBe('board');
    act(() => {
      takePaletteQuery();
    });
    expect(result.current).toBe('');
  });
});
