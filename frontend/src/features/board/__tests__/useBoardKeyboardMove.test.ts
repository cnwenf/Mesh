/**
 * 键盘移动模式 hook 单元测试(design-quality §9.4.5):进入/选列(左右循环)/
 * 选位(上下钳制)/确认/取消/禁用/空列回退。经 renderHook 直接驱动,
 * 补齐经组件难以触达的边界分支。
 */
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useBoardKeyboardMove } from '../useBoardKeyboardMove';
import type { UseBoardKeyboardMoveOptions } from '../useBoardKeyboardMove';

function setup(overrides: Partial<UseBoardKeyboardMoveOptions> = {}) {
  const announce = vi.fn();
  const onDropCard = vi.fn();
  const cardCounts: Record<string, number> = { todo: 2, in_progress: 1, done: 0 };
  const options: UseBoardKeyboardMoveOptions = {
    enabled: true,
    columns: ['todo', 'in_progress', 'done'],
    getCardCount: (key) => cardCounts[key] ?? 0,
    getColumnLabel: (key) => `列-${key}`,
    onDropCard,
    computePosition: (_columnKey, index) => (index === null ? 999 : index * 10),
    announce,
    t: (key) => key,
    ...overrides,
  };
  const hook = renderHook(() => useBoardKeyboardMove(options));
  return { hook, announce, onDropCard };
}

/** 构造一个最小 React.KeyboardEvent 桩(仅用到 key + preventDefault)。 */
function keyEvent(key: string): React.KeyboardEvent {
  return { key, preventDefault: vi.fn() } as unknown as React.KeyboardEvent;
}

describe('useBoardKeyboardMove', () => {
  it('方向键进入移动模式并播报说明', () => {
    const { hook, announce } = setup();
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowDown'), 'a', 'WEB-A', 'todo');
    });
    expect(hook.result.current.moveState).toMatchObject({ cardId: 'a', targetColumnKey: 'todo' });
    expect(announce).toHaveBeenCalledWith('board.moveModeEntered');
  });

  it('非方向键(未进入模式)为无操作', () => {
    const { hook, announce } = setup();
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('a'), 'a', 'WEB-A', 'todo');
    });
    expect(hook.result.current.moveState).toBeNull();
    expect(announce).not.toHaveBeenCalled();
  });

  it('未进入模式时 Enter/Escape 为无操作', () => {
    const { hook, onDropCard, announce } = setup();
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('Enter'), 'a', 'WEB-A', 'todo');
      hook.result.current.handleCardKeyDown(keyEvent('Escape'), 'a', 'WEB-A', 'todo');
    });
    expect(onDropCard).not.toHaveBeenCalled();
    expect(announce).not.toHaveBeenCalled();
  });

  it('disabled 时完全无操作', () => {
    const { hook, announce } = setup({ enabled: false });
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowRight'), 'a', 'WEB-A', 'todo');
    });
    expect(hook.result.current.moveState).toBeNull();
    expect(announce).not.toHaveBeenCalled();
  });

  it('左右键循环选列(含回绕)', () => {
    const { hook } = setup();
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowLeft'), 'a', 'WEB-A', 'todo');
    });
    // 进入后目标列即 todo;再左 → 回绕到 done。
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowLeft'), 'a', 'WEB-A', 'todo');
    });
    expect(hook.result.current.moveState?.targetColumnKey).toBe('done');
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowRight'), 'a', 'WEB-A', 'todo');
    });
    expect(hook.result.current.moveState?.targetColumnKey).toBe('todo');
  });

  it('上下键在 [0, count] 内钳制位置', () => {
    const { hook } = setup();
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowDown'), 'a', 'WEB-A', 'todo'); // 进入 index1
    });
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowUp'), 'a', 'WEB-A', 'todo'); // → 0
    });
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowUp'), 'a', 'WEB-A', 'todo'); // 钳制 0
    });
    expect(hook.result.current.moveState?.targetIndex).toBe(0);
    // 连续下移到 count(2)后钳制(每次独立 act 以获得最新闭包)。
    for (let i = 0; i < 3; i++) {
      act(() => {
        hook.result.current.handleCardKeyDown(keyEvent('ArrowDown'), 'a', 'WEB-A', 'todo');
      });
    }
    expect(hook.result.current.moveState?.targetIndex).toBe(2);
  });

  it('Enter 确认:列底位置用 null index', () => {
    const { hook, onDropCard, announce } = setup();
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowRight'), 'a', 'WEB-A', 'done'); // 进入(done 空列)
    });
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('Enter'), 'a', 'WEB-A', 'done');
    });
    // done 空列:targetIndex(0) >= count(0) → null → computePosition 999。
    expect(onDropCard).toHaveBeenCalledWith('a', 'done', 999);
    expect(announce).toHaveBeenCalledWith('board.moveConfirmed');
    expect(hook.result.current.moveState).toBeNull();
  });

  it('Esc 取消移动模式并播报', () => {
    const { hook, announce, onDropCard } = setup();
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowDown'), 'a', 'WEB-A', 'todo');
    });
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('Escape'), 'a', 'WEB-A', 'todo');
    });
    expect(announce).toHaveBeenCalledWith('board.moveCancelled');
    expect(hook.result.current.moveState).toBeNull();
    expect(onDropCard).not.toHaveBeenCalled();
  });

  it('移动模式中忽略其它卡片的按键', () => {
    const { hook, onDropCard } = setup();
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowDown'), 'a', 'WEB-A', 'todo');
    });
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('Enter'), 'b', 'WEB-B', 'todo');
    });
    expect(onDropCard).not.toHaveBeenCalled();
    expect(hook.result.current.moveState?.cardId).toBe('a');
  });

  it('空列集合:左右键回退到当前目标列', () => {
    const { hook } = setup({ columns: [] });
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowRight'), 'a', 'WEB-A', 'todo');
    });
    // columns 为空 → indexOf=-1,(−1+1)%0=NaN → columns[NaN] 未定义 → 回退当前列。
    act(() => {
      hook.result.current.handleCardKeyDown(keyEvent('ArrowRight'), 'a', 'WEB-A', 'todo');
    });
    expect(hook.result.current.moveState?.targetColumnKey).toBe('todo');
  });

  it('cancelMove 空闲时为无操作(不播报)', () => {
    const { hook, announce } = setup();
    act(() => {
      hook.result.current.cancelMove();
    });
    expect(announce).not.toHaveBeenCalled();
  });
});
