import { useState } from 'react';
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_TOAST_DURATION_MS, ToastProvider, useToast } from '../components/Toast';
import type { ToastTimer } from '../components/Toast';

interface TimerEntry {
  fn: () => void;
  ms: number;
  cleared: boolean;
}

interface TimerStub extends ToastTimer {
  calls: ReadonlyArray<{ ms: number }>;
  fire: (index: number) => void;
  clearedCount: number;
}

function createTimerStub(): TimerStub {
  const entries: TimerEntry[] = [];
  return {
    setTimeout: (fn, ms) => {
      entries.push({ fn, ms, cleared: false });
      return entries.length;
    },
    clearTimeout: (handle) => {
      const entry = entries[handle - 1];
      if (entry) entry.cleared = true;
    },
    get calls() {
      return entries.map(({ ms }) => ({ ms }));
    },
    fire: (index) => {
      const entry = entries[index];
      if (entry && !entry.cleared) entry.fn();
    },
    get clearedCount() {
      return entries.filter((entry) => entry.cleared).length;
    },
  };
}

interface HarnessProps {
  onAction?: () => void;
  onId?: (id: string) => void;
}

function ToastHarness({ onAction, onId }: HarnessProps): React.JSX.Element {
  const { addToast, dismissToast } = useToast();
  const [lastId, setLastId] = useState('');
  return (
    <div>
      <button
        type="button"
        onClick={() => {
          const id = addToast('Saved', { closeLabel: 'Close toast', tone: 'success' });
          setLastId(id);
          onId?.(id);
        }}
      >
        add-plain
      </button>
      <button
        type="button"
        onClick={() =>
          addToast('Deleted', {
            closeLabel: 'Close toast',
            tone: 'danger',
            actionLabel: 'Undo',
            onAction,
            durationMs: 9000,
          })
        }
      >
        add-action
      </button>
      <button
        type="button"
        onClick={() => addToast('No action handler', { closeLabel: 'Close toast', actionLabel: 'Ghost' })}
      >
        add-label-only
      </button>
      <button type="button" onClick={() => dismissToast(lastId)}>
        dismiss-last
      </button>
    </div>
  );
}

afterEach(() => {
  vi.useRealTimers();
});

describe('ToastProvider + useToast', () => {
  it('渲染 aria-live=polite 的 live region(aria-label 来自 prop)', () => {
    render(
      <ToastProvider regionLabel="Notifications">
        <div />
      </ToastProvider>,
    );
    const region = screen.getByRole('status', { name: 'Notifications' });
    expect(region).toHaveAttribute('aria-live', 'polite');
  });

  it('addToast 显示消息,tone 落到类名;closeLabel 成为关闭按钮可访问名', () => {
    const timer = createTimerStub();
    render(
      <ToastProvider regionLabel="Notifications" timer={timer}>
        <ToastHarness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'add-plain' }));
    const toast = screen.getByText('Saved');
    expect(toast.closest('.mesh-toast')?.className).toContain('mesh-toast--success');
    fireEvent.click(screen.getByRole('button', { name: 'Close toast' }));
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  });

  it('自动消失:默认 5000ms(注入计时器验证时长与触发)', () => {
    const timer = createTimerStub();
    render(
      <ToastProvider regionLabel="Notifications" timer={timer}>
        <ToastHarness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'add-plain' }));
    expect(timer.calls).toEqual([{ ms: DEFAULT_TOAST_DURATION_MS }]);
    act(() => timer.fire(0));
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  });

  it('durationMs 覆盖自动消失时长', () => {
    const timer = createTimerStub();
    render(
      <ToastProvider regionLabel="Notifications" timer={timer}>
        <ToastHarness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'add-action' }));
    expect(timer.calls).toEqual([{ ms: 9000 }]);
  });

  it('默认计时器走 window.setTimeout(fake timers 验证真实自动消失)', () => {
    vi.useFakeTimers();
    render(
      <ToastProvider regionLabel="Notifications">
        <ToastHarness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'add-plain' }));
    expect(screen.getByText('Saved')).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(DEFAULT_TOAST_DURATION_MS);
    });
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  });

  it('手动关闭同时取消挂起的自动消失计时器', () => {
    const timer = createTimerStub();
    render(
      <ToastProvider regionLabel="Notifications" timer={timer}>
        <ToastHarness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'add-plain' }));
    expect(timer.clearedCount).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: 'Close toast' }));
    expect(timer.clearedCount).toBe(1);
  });

  it('action 按钮执行 onAction 并关闭该 toast', () => {
    const onAction = vi.fn();
    const timer = createTimerStub();
    render(
      <ToastProvider regionLabel="Notifications" timer={timer}>
        <ToastHarness onAction={onAction} />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'add-action' }));
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Deleted')).not.toBeInTheDocument();
  });

  it('仅有 actionLabel 无 onAction 时不渲染动作按钮(避免死按钮)', () => {
    const timer = createTimerStub();
    render(
      <ToastProvider regionLabel="Notifications" timer={timer}>
        <ToastHarness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'add-label-only' }));
    expect(screen.queryByRole('button', { name: 'Ghost' })).not.toBeInTheDocument();
  });

  it('dismissToast(id) 按返回值精确关闭', () => {
    const timer = createTimerStub();
    const ids: string[] = [];
    render(
      <ToastProvider regionLabel="Notifications" timer={timer}>
        <ToastHarness onId={(id) => ids.push(id)} />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'add-plain' }));
    expect(ids).toHaveLength(1);
    expect(ids[0]).toBeTypeOf('string');
    fireEvent.click(screen.getByRole('button', { name: 'dismiss-last' }));
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  });

  it('卸载时清理全部挂起计时器', () => {
    const timer = createTimerStub();
    const { unmount } = render(
      <ToastProvider regionLabel="Notifications" timer={timer}>
        <ToastHarness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'add-plain' }));
    fireEvent.click(screen.getByRole('button', { name: 'add-action' }));
    expect(timer.calls).toHaveLength(2);
    unmount();
    expect(timer.clearedCount).toBe(2);
  });

  it('多条 toast 并存,互不影响', () => {
    const timer = createTimerStub();
    render(
      <ToastProvider regionLabel="Notifications" timer={timer}>
        <ToastHarness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'add-plain' }));
    fireEvent.click(screen.getByRole('button', { name: 'add-action' }));
    expect(screen.getByText('Saved')).toBeInTheDocument();
    expect(screen.getByText('Deleted')).toBeInTheDocument();
    act(() => timer.fire(0));
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
    expect(screen.getByText('Deleted')).toBeInTheDocument();
  });

  it('useToast 在 Provider 外使用抛错(开发者错误,非 UI 文案)', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => renderHook(() => useToast())).toThrow(/ToastProvider/);
    errorSpy.mockRestore();
  });
});
