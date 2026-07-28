/**
 * 任务编排流订阅 hook(squad.md §3.5):非终态时以 fetch 流式消费 SSE(认证兼容,
 * 见 stream.ts),命中五类事件即触发上层重取;流不可用(HTTP 失败 / 无主体)时静默
 * 退出,由既有 3s 轮询兜底(§3.5 降级)。正常收尾时按 `Last-Event-ID` 重连续传
 * (上限内),AbortSignal 于卸载 / 终态时收口。
 */
import { useEffect, useRef } from 'react';
import { getToken } from '../../api';
import { TASK_STREAM_EVENTS, connectTaskStream } from './stream';

const MAX_STREAM_ATTEMPTS = 3;
const RECONNECT_DELAY_MS = 1000;

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export interface UseTaskStreamOptions {
  /** 流绝对 URL;null(工作区 / 参数未就绪)时不连接。 */
  readonly url: string | null;
  /** 仅非终态为 true;终态即断流。 */
  readonly enabled: boolean;
  /** 命中编排事件时的重取回调(经 ref 读取,身份变化不重连)。 */
  readonly onEvent: () => void;
}

export function useTaskStream(options: UseTaskStreamOptions): void {
  const { url, enabled } = options;
  const onEventRef = useRef(options.onEvent);
  onEventRef.current = options.onEvent;

  useEffect(() => {
    if (url === null || !enabled) return;
    let active = true;
    const aborter = new AbortController();

    void (async () => {
      let lastId = 0;
      let attempts = 0;
      while (active && attempts < MAX_STREAM_ATTEMPTS) {
        try {
          lastId = await connectTaskStream({
            url,
            getToken,
            lastEventId: lastId,
            signal: aborter.signal,
            onFrame: (frame) => {
              if (TASK_STREAM_EVENTS.has(frame.event)) onEventRef.current();
            },
          });
        } catch {
          // 流不可用(网络 / HTTP 失败 / 无主体):退出,轮询兜底(§3.5)。
          return;
        }
        if (!active) return;
        // 正常收尾(服务端结束):按 Last-Event-ID 重连续传,上限内退避。
        attempts += 1;
        if (attempts >= MAX_STREAM_ATTEMPTS) return;
        await delay(RECONNECT_DELAY_MS, aborter.signal);
      }
    })();

    return () => {
      active = false;
      aborter.abort();
    };
  }, [url, enabled]);
}
