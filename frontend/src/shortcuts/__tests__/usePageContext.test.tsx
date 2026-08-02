/**
 * usePageContext — 页面上下文激活/卸载复位(setContexts 死代码接通,§5.1)。
 */
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useShortcutRegistry } from '../registry';
import { usePageContext } from '../usePageContext';

beforeEach(() => {
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
});

describe('usePageContext', () => {
  it('挂载写入 [global, ...contexts](global 恒前置)', () => {
    renderHook(() => usePageContext('board'));
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global', 'board']);
  });

  it('issue 详情叠加 board(仲裁 issue 胜出所需上下文形态)', () => {
    renderHook(() => usePageContext('board', 'issue'));
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global', 'board', 'issue']);
  });

  it('chat 独占:仅 [global, chat]', () => {
    renderHook(() => usePageContext('chat'));
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global', 'chat']);
  });

  it('卸载复位为 [global]', () => {
    const { unmount } = renderHook(() => usePageContext('board'));
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global', 'board']);
    unmount();
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global']);
  });

  it('页面切换:旧页卸载复位后新页激活(提交序:cleanup 先于 effect)', () => {
    const first = renderHook(() => usePageContext('chat'));
    first.unmount();
    const second = renderHook(() => usePageContext('board'));
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global', 'board']);
    second.unmount();
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global']);
  });

  it('无参数调用仅激活 global', () => {
    renderHook(() => usePageContext());
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global']);
  });

  it('contexts 字面量变化时重设(同一钩子内切换上下文)', () => {
    let ctx: 'board' | 'issue' = 'board';
    const { rerender } = renderHook(() => usePageContext(ctx));
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global', 'board']);
    ctx = 'issue';
    act(() => rerender());
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global', 'issue']);
  });
});
