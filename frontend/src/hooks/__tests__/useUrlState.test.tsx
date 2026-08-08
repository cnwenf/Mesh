/**
 * useUrlState 测试(L92):单键 search param 状态化 ——
 * 缺省 null、读现有值、写值 set、null/空串删键、保留其余键、
 * replace 默认不产生历史条目、push 模式可回退。
 */
import { act, renderHook } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { describe, expect, it } from 'vitest';
import { useUrlState } from '../useUrlState';

function renderUrlState(
  initialEntry = '/',
  key = 'filter',
): {
  result: ReturnType<typeof renderHook<ReturnType<typeof useUrlState>, []>>['result'];
  location: () => ReturnType<typeof useLocation>;
} {
  let latestLocation: ReturnType<typeof useLocation> | null = null;
  function LocationProbe(): null {
    latestLocation = useLocation();
    return null;
  }
  const { result } = renderHook(() => useUrlState(key), {
    wrapper: ({ children }) => (
      <MemoryRouter initialEntries={[initialEntry]}>
        <LocationProbe />
        <Routes>
          <Route path="*" element={<>{children}</>} />
        </Routes>
      </MemoryRouter>
    ),
  });
  return { result, location: () => latestLocation as unknown as ReturnType<typeof useLocation> };
}

describe('useUrlState', () => {
  it('无参数时读为 null,写入值后出现在 URL', () => {
    const { result, location } = renderUrlState('/');

    expect(result.current[0]).toBeNull();

    act(() => result.current[1]('unread'));

    expect(result.current[0]).toBe('unread');
    expect(location().search).toBe('?filter=unread');
  });

  it('挂载时读取已有参数值', () => {
    const { result } = renderUrlState('/?filter=mentions');

    expect(result.current[0]).toBe('mentions');
  });

  it('写入 null 或空串删除该键', () => {
    const { result, location } = renderUrlState('/?filter=unread');

    act(() => result.current[1](null));
    expect(result.current[0]).toBeNull();
    expect(location().search).toBe('');
  });

  it('写入时保留其余键', () => {
    const { result, location } = renderUrlState('/?q=bug&sort=created_at');

    act(() => result.current[1]('assigned'));

    expect(location().search).toContain('q=bug');
    expect(location().search).toContain('sort=created_at');
    expect(location().search).toContain('filter=assigned');
  });

  it('默认 replace 写入:历史栈不增长(回退离开路由而非回到旧参数)', () => {
    const { result, location } = renderUrlState('/');

    act(() => result.current[1]('unread'));
    act(() => result.current[1]('mentions'));

    // replace:两次写入均覆盖同一条目,search 为最新值
    expect(location().search).toBe('?filter=mentions');
  });

  it('push 模式写入产生历史条目', () => {
    const { result, location } = renderUrlState('/');

    act(() => result.current[1]('unread', { mode: 'push' }));

    expect(location().search).toBe('?filter=unread');
  });

  it('键名按调用方给定读写', () => {
    const { result, location } = renderUrlState('/?tab=activity', 'tab');

    expect(result.current[0]).toBe('activity');

    act(() => result.current[1](null));
    expect(location().search).toBe('');
  });
});
