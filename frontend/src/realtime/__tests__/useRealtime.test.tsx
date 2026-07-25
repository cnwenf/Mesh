import { StrictMode } from 'react';
import type { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useRealtime } from '../useRealtime';
import { FakeWebSocket, FakeWebSocketImpl } from './FakeWebSocket';

const URL = 'ws://host/ws';

function baseOptions() {
  return { url: URL, getToken: () => 'tok', WebSocketImpl: FakeWebSocketImpl };
}

beforeEach(() => {
  FakeWebSocket.reset();
  localStorage.clear();
});

describe('useRealtime', () => {
  it('creates a client and connects on mount, exposing live state', () => {
    const { result } = renderHook(() => useRealtime(baseOptions()));
    expect(result.current.client).toBeDefined();
    expect(result.current.state).toBe('connecting');
    act(() => {
      FakeWebSocket.last.open();
    });
    expect(result.current.state).toBe('connected');
  });

  it('disconnects on unmount', () => {
    const { unmount } = renderHook(() => useRealtime(baseOptions()));
    const socket = FakeWebSocket.last;
    act(() => {
      socket.open();
    });
    unmount();
    expect(socket.closeCalled).toBe(true);
  });

  it('does not connect when enabled === false', () => {
    const { result } = renderHook(() => useRealtime({ ...baseOptions(), enabled: false }));
    expect(result.current.state).toBe('idle');
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('connects when enabled flips from false to true', () => {
    const { result, rerender } = renderHook(
      ({ enabled }) => useRealtime({ ...baseOptions(), enabled }),
      { initialProps: { enabled: false } },
    );
    expect(FakeWebSocket.instances).toHaveLength(0);
    rerender({ enabled: true });
    expect(result.current.state).toBe('connecting');
  });

  it('returns a stable client across re-renders', () => {
    const { result, rerender } = renderHook(() => useRealtime(baseOptions()));
    const first = result.current.client;
    rerender();
    expect(result.current.client).toBe(first);
  });

  it('is strict-mode double-mount safe', () => {
    function wrapper({ children }: { children: ReactNode }) {
      return <StrictMode>{children}</StrictMode>;
    }
    const { result } = renderHook(() => useRealtime(baseOptions()), { wrapper });
    act(() => {
      FakeWebSocket.last.open();
    });
    expect(result.current.state).toBe('connected');
  });

  it('reports offline through state when no token is available', () => {
    const { result } = renderHook(() =>
      useRealtime({ url: URL, getToken: () => null, WebSocketImpl: FakeWebSocketImpl }),
    );
    expect(result.current.state).toBe('offline');
    expect(FakeWebSocket.instances).toHaveLength(0);
  });
});
