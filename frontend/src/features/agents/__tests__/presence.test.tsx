/**
 * presence 订阅 hook 测试(README §6.7/§6.12,design-quality.md §9.8)。
 * - parsePresenceFrame 纯函数各分支(频道匹配 / 异频道 / 缺字段回退 0);
 * - useAgentPresenceMap 经 RealtimeContext.Provider 假客户端驱动:
 *   null realtime → 空映射;逐 id 订阅/退订;帧 → 三元组合并;异频道忽略;空列表不订阅。
 * 沿用既有 realtime 测试模式(组件内消费 hook + 假客户端发帧)。
 */
import { act, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { RealtimeContext } from '../../../shell/AppShell';
import { parsePresenceFrame, useAgentPresenceMap } from '../presence';
import type { RealtimeEventFrame } from '../../../types/realtime';

afterEach(() => {
  vi.restoreAllMocks();
});

function makeFrame(channel: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel, seq: 1, event: 'agent.presence', payload } as RealtimeEventFrame;
}

describe('parsePresenceFrame', () => {
  it('agent presence 频道 → { id, triple }', () => {
    const parsed = parsePresenceFrame(
      makeFrame('agent:agt-1:presence', { running: 1, queued: 2, awaiting_approval: 3 }),
    );
    expect(parsed).toEqual({ id: 'agt-1', triple: { running: 1, queued: 2, awaiting: 3 } });
  });

  it('非 presence 频道 → null(workspace 域频道)', () => {
    expect(parsePresenceFrame(makeFrame('workspace:ws-1:agents', {}))).toBeNull();
  });

  it('其它 agent 域非 presence 频道 → null', () => {
    expect(parsePresenceFrame(makeFrame('agent:agt-1:lifecycle', {}))).toBeNull();
  });

  it('payload 缺字段回退 0', () => {
    const parsed = parsePresenceFrame(makeFrame('agent:agt-2:presence', {}));
    expect(parsed).toEqual({ id: 'agt-2', triple: { running: 0, queued: 0, awaiting: 0 } });
  });

  it('payload 部分字段:仅 running,其余回退 0', () => {
    const parsed = parsePresenceFrame(makeFrame('agent:agt-3:presence', { running: 5 }));
    expect(parsed).toEqual({ id: 'agt-3', triple: { running: 5, queued: 0, awaiting: 0 } });
  });
});

interface FakeFrame {
  channel: string;
  payload?: unknown;
}

function makeFakeRealtime() {
  const handlers: Array<(frame: FakeFrame) => void> = [];
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((handler: (frame: FakeFrame) => void) => {
      handlers.push(handler);
      return (): void => {
        const index = handlers.indexOf(handler);
        if (index >= 0) handlers.splice(index, 1);
      };
    }),
  };
  return {
    client,
    emit: (frame: FakeFrame): void => {
      for (const handler of [...handlers]) handler(frame);
    },
  };
}

/** 组件内消费 hook,把映射序列化为 JSON 供断言(沿用仓库「组件内测 hook」模式)。 */
function PresenceProbe(props: {
  ids: readonly string[];
  initial?: ReadonlyMap<string, { running: number; queued: number; awaiting: number }>;
}): React.JSX.Element {
  const map = useAgentPresenceMap(props.ids, props.initial);
  return <div data-testid="presence-probe">{JSON.stringify([...map.entries()])}</div>;
}

function renderWithRealtime(
  realtime: unknown,
  ids: readonly string[],
  initial?: ReadonlyMap<string, { running: number; queued: number; awaiting: number }>,
) {
  return renderWithProviders(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- 测试替身,非生产形状
    <RealtimeContext.Provider value={realtime as any}>
      <PresenceProbe ids={ids} initial={initial} />
    </RealtimeContext.Provider>,
  );
}

describe('useAgentPresenceMap', () => {
  it('shell 外(realtime 为 null)→ 恒空映射', () => {
    renderWithProviders(<PresenceProbe ids={['agt-1']} />);
    expect(screen.getByTestId('presence-probe').textContent).toBe('[]');
  });

  it('REST 初始快照立即可用,实时绝对帧随后整体覆盖', async () => {
    const rt = makeFakeRealtime();
    const initial = new Map([['agt-1', { running: 4, queued: 5, awaiting: 6 }]]);
    renderWithRealtime(rt, ['agt-1'], initial);
    expect(screen.getByTestId('presence-probe').textContent).toBe(
      JSON.stringify([['agt-1', { running: 4, queued: 5, awaiting: 6 }]]),
    );

    act(() => {
      rt.emit({
        channel: 'agent:agt-1:presence',
        payload: { running: 1, queued: 2, awaiting_approval: 3 },
      });
    });
    await waitFor(() =>
      expect(screen.getByTestId('presence-probe').textContent).toBe(
        JSON.stringify([['agt-1', { running: 1, queued: 2, awaiting: 3 }]]),
      ),
    );
  });

  it('空 id 列表 → 空映射且不订阅', () => {
    const rt = makeFakeRealtime();
    renderWithRealtime(rt, []);
    expect(screen.getByTestId('presence-probe').textContent).toBe('[]');
    expect(rt.client.subscribe).not.toHaveBeenCalled();
  });

  it('逐 id 订阅 presence 频道', () => {
    const rt = makeFakeRealtime();
    renderWithRealtime(rt, ['agt-1', 'agt-2']);
    expect(rt.client.subscribe).toHaveBeenCalledWith('agent:agt-1:presence');
    expect(rt.client.subscribe).toHaveBeenCalledWith('agent:agt-2:presence');
  });

  it('帧 → 三元组合并到映射', async () => {
    const rt = makeFakeRealtime();
    renderWithRealtime(rt, ['agt-1']);
    act(() => {
      rt.emit({
        channel: 'agent:agt-1:presence',
        payload: { running: 1, queued: 2, awaiting_approval: 3 },
      });
    });
    await waitFor(() =>
      expect(screen.getByTestId('presence-probe').textContent).toBe(
        JSON.stringify([['agt-1', { running: 1, queued: 2, awaiting: 3 }]]),
      ),
    );
  });

  it('异频道帧忽略(非订阅 agent)', async () => {
    const rt = makeFakeRealtime();
    renderWithRealtime(rt, ['agt-1']);
    act(() => {
      rt.emit({ channel: 'agent:agt-9:presence', payload: { running: 9 } });
    });
    act(() => {
      rt.emit({ channel: 'workspace:ws-1:agents', payload: { running: 9 } });
    });
    // 映射保持空(异频道未收敛)
    expect(screen.getByTestId('presence-probe').textContent).toBe('[]');
  });

  it('多 agent 帧分别合并,后到帧覆盖先到', async () => {
    const rt = makeFakeRealtime();
    renderWithRealtime(rt, ['agt-1', 'agt-2']);
    act(() => {
      rt.emit({ channel: 'agent:agt-1:presence', payload: { running: 1 } });
    });
    act(() => {
      rt.emit({ channel: 'agent:agt-2:presence', payload: { queued: 2 } });
    });
    act(() => {
      rt.emit({ channel: 'agent:agt-1:presence', payload: { running: 0 } });
    });
    await waitFor(() =>
      expect(screen.getByTestId('presence-probe').textContent).toBe(
        JSON.stringify([
          ['agt-1', { running: 0, queued: 0, awaiting: 0 }],
          ['agt-2', { running: 0, queued: 2, awaiting: 0 }],
        ]),
      ),
    );
  });

  it('id 列表变化:退订旧频道、订阅新频道', () => {
    const rt = makeFakeRealtime();
    const { rerender } = renderWithProviders(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- 测试替身
      <RealtimeContext.Provider value={rt as any}>
        <PresenceProbe ids={['agt-1']} />
      </RealtimeContext.Provider>,
    );
    expect(rt.client.subscribe).toHaveBeenCalledWith('agent:agt-1:presence');
    rerender(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- 测试替身
      <RealtimeContext.Provider value={rt as any}>
        <PresenceProbe ids={['agt-2']} />
      </RealtimeContext.Provider>,
    );
    expect(rt.client.unsubscribe).toHaveBeenCalledWith('agent:agt-1:presence');
    expect(rt.client.subscribe).toHaveBeenCalledWith('agent:agt-2:presence');
  });

  it('卸载时退订全部频道并解绑帧监听', () => {
    const rt = makeFakeRealtime();
    const { unmount } = renderWithRealtime(rt, ['agt-1', 'agt-2']);
    unmount();
    expect(rt.client.unsubscribe).toHaveBeenCalledWith('agent:agt-1:presence');
    expect(rt.client.unsubscribe).toHaveBeenCalledWith('agent:agt-2:presence');
    // 帧监听解绑:再发帧不触发状态更新(无异常即可)
    act(() => {
      rt.emit({ channel: 'agent:agt-1:presence', payload: { running: 1 } });
    });
  });
});
