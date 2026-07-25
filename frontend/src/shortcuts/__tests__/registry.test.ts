import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useShortcutRegistry } from '../registry';
import type { ShortcutContext } from '../registry';

beforeEach(() => {
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
});

describe('useShortcutRegistry(分组注册表,不可变更新)', () => {
  it('registerCommand 添加命令,返回的函数注销', () => {
    let unregister: (() => void) | undefined;
    act(() => {
      unregister = useShortcutRegistry
        .getState()
        .registerCommand({ id: 'a', label: 'A', group: 'global', run: vi.fn() });
    });
    expect(useShortcutRegistry.getState().commands).toHaveLength(1);
    expect(useShortcutRegistry.getState().commands[0]?.id).toBe('a');
    act(() => unregister?.());
    expect(useShortcutRegistry.getState().commands).toHaveLength(0);
  });

  it('同 id 重复 registerCommand 替换而非堆叠', () => {
    act(() => {
      useShortcutRegistry.getState().registerCommand({ id: 'x', label: 'old', group: 'global', run: vi.fn() });
      useShortcutRegistry.getState().registerCommand({ id: 'x', label: 'new', group: 'global', run: vi.fn() });
    });
    const commands = useShortcutRegistry.getState().commands;
    expect(commands).toHaveLength(1);
    expect(commands[0]?.label).toBe('new');
  });

  it('registerShortcuts 批量注册并整体注销', () => {
    let unregister: (() => void) | undefined;
    act(() => {
      unregister = useShortcutRegistry.getState().registerShortcuts([
        { id: 's1', combo: 'c', label: 'S1', group: 'global', run: vi.fn() },
        { id: 's2', combo: '/', label: 'S2', group: 'global', run: vi.fn() },
      ]);
    });
    expect(useShortcutRegistry.getState().shortcuts).toHaveLength(2);
    act(() => unregister?.());
    expect(useShortcutRegistry.getState().shortcuts).toHaveLength(0);
  });

  it('同 id 批量注册替换旧定义', () => {
    act(() => {
      useShortcutRegistry.getState().registerShortcuts([
        { id: 'k', combo: 'c', label: 'old', group: 'global', run: vi.fn() },
      ]);
      useShortcutRegistry.getState().registerShortcuts([
        { id: 'k', combo: 'c', label: 'new', group: 'global', run: vi.fn() },
      ]);
    });
    const shortcuts = useShortcutRegistry.getState().shortcuts;
    expect(shortcuts).toHaveLength(1);
    expect(shortcuts[0]?.label).toBe('new');
  });

  it('setContexts 拷贝传入数组(外部变更不影响内部状态)', () => {
    const contexts: ShortcutContext[] = ['board'];
    act(() => useShortcutRegistry.getState().setContexts(contexts));
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['board']);
    expect(useShortcutRegistry.getState().activeContexts).not.toBe(contexts);
    contexts.push('issue');
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['board']);
  });

  it('状态更新不可变:旧快照不受后续写入影响', () => {
    const before = useShortcutRegistry.getState();
    act(() => {
      useShortcutRegistry.getState().registerCommand({ id: 'z', label: 'Z', group: 'global', run: vi.fn() });
    });
    expect(before.commands).toHaveLength(0);
    expect(useShortcutRegistry.getState().commands).toHaveLength(1);
    expect(useShortcutRegistry.getState()).not.toBe(before);
  });

  it('hook 订阅可观察注册变化', () => {
    const { result } = renderHook(() => useShortcutRegistry((state) => state.commands));
    expect(result.current).toHaveLength(0);
    act(() => {
      useShortcutRegistry.getState().registerCommand({ id: 'h', label: 'H', group: 'global', run: vi.fn() });
    });
    expect(result.current).toHaveLength(1);
  });
});
