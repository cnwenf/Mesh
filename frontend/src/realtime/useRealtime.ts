/**
 * RealtimeClient 的 React 绑定。
 * - 组件生命周期内持有一个 RealtimeClient(按 url 稳定)
 * - 挂载(且 enabled !== false)时 connect,卸载时 disconnect
 * - 经 onState 暴露实时 state;StrictMode 双挂载安全(disconnect 后可再 connect)
 */
import { useEffect, useRef, useState } from 'react';
import { RealtimeClient } from './RealtimeClient';
import type { ConnectionState, RealtimeClientOptions } from './RealtimeClient';

export interface UseRealtimeOptions extends Omit<RealtimeClientOptions, 'WebSocketImpl'> {
  WebSocketImpl?: typeof WebSocket;
  /** 默认 true;false 时不建连 */
  enabled?: boolean;
}

export interface UseRealtimeResult {
  state: ConnectionState;
  client: RealtimeClient;
}

export function useRealtime(options: UseRealtimeOptions): UseRealtimeResult {
  const { enabled = true, ...clientOptions } = options;

  const clientRef = useRef<RealtimeClient | null>(null);
  if (clientRef.current === null) {
    clientRef.current = new RealtimeClient(clientOptions);
  }
  const client = clientRef.current;

  const [state, setState] = useState<ConnectionState>(client.state);

  useEffect(() => {
    const unsubscribe = client.onState(setState);
    setState(client.state);
    if (enabled) client.connect();
    return () => {
      unsubscribe();
      client.disconnect();
    };
  }, [client, enabled]);

  return { state, client };
}
