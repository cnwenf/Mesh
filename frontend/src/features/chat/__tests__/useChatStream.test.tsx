/**
 * useChatStream hook 测试(chat-session.md §3.3):created/delta/done 累积成实时消息、
 * interrupted 取 partial_content、error 置失败态 + streamError、abort/reset 收口。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useChatStream } from '../useChatStream';

const encoder = new TextEncoder();

function streamOf(frames: readonly string[]): typeof fetch {
  return (async () => ({
    ok: true,
    status: 200,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
        controller.close();
      },
    }),
    headers: { get: () => null },
  })) as unknown as typeof fetch;
}

interface RecordedCall {
  url: string;
  init?: RequestInit;
}

/** 闲置开放流(不关闭),记录每次调用;监听本次连接 abort 信号以支持 nudge 续传(§5.4)。 */
function openStreamFetch(calls: RecordedCall[], frame: string): typeof fetch {
  return (async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frame));
        init?.signal?.addEventListener('abort', () => {
          try {
            controller.error(new Error('aborted'));
          } catch {
            /* 已关闭则忽略 */
          }
        });
      },
    });
    return { ok: true, status: 200, body, headers: { get: () => null } } as unknown as Response;
  }) as typeof fetch;
}

/** 以可写方式覆写 document.visibilityState(jsdom 默认只读),返回还原函数。 */
function stubVisibility(state: string): () => void {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
  return () => {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
  };
}

afterEach(() => stubVisibility('visible')());

const created =
  'id: 1\nevent: message.created\ndata: {"message_id":"m-1","role":"agent","generation_status":"streaming"}\n\n';

describe('useChatStream(§3.3 单次生成驱动)', () => {
  it('created + delta + done 累积为完成态实时消息', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: message.delta\ndata: {"message_id":"m-1","delta":"Hello"}\n\n',
      'id: 3\nevent: message.delta\ndata: {"message_id":"m-1","delta":" world"}\n\n',
      'id: 4\nevent: message.done\ndata: {"message_id":"m-1","generation_status":"done","completion_tokens":5}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'gen-1',
        sessionId: 'sess-1',
      });
    });
    expect(result.current.isStreaming).toBe(true);
    expect(result.current.activeGenerationId).toBe('gen-1');
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    const message = result.current.liveMessage;
    expect(message).not.toBeNull();
    expect(message?.id).toBe('m-1');
    expect(message?.content).toBe('Hello world');
    expect(message?.generation_status).toBe('done');
    expect(message?.completion_tokens).toBe(5);
    expect(message?.session_id).toBe('sess-1');
  });

  it('interrupted 取 partial_content 作为正文', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: message.delta\ndata: {"message_id":"m-1","delta":"abc"}\n\n',
      'id: 3\nevent: message.interrupted\ndata: {"message_id":"m-1","partial_content":"ab","generation_status":"interrupted"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'gen-1',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.liveMessage?.content).toBe('ab');
    expect(result.current.liveMessage?.generation_status).toBe('interrupted');
  });

  it('interrupted 空 partial_content 保留已累积正文', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: message.delta\ndata: {"message_id":"m-1","delta":"keep"}\n\n',
      'id: 3\nevent: message.interrupted\ndata: {"message_id":"m-1","partial_content":"","generation_status":"interrupted"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'gen-1',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.liveMessage?.content).toBe('keep');
  });

  it('error 事件置失败态 + streamError 键', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: error\ndata: {"message_id":"m-1","code":"rate_limited","message":"slow down"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'gen-1',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.streamError).toBe('error.rate_limited');
    expect(result.current.liveMessage?.generation_status).toBe('failed');
    expect(result.current.liveMessage?.error_message).toBe('slow down');
  });

  it('abort 停止流并置 isStreaming=false', async () => {
    // 永不结束的流(无终态):保持 streaming 直到 abort。
    const fetchImpl = streamOf([created]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'gen-1',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.liveMessage).not.toBeNull());
    act(() => {
      result.current.abort();
    });
    expect(result.current.isStreaming).toBe(false);
  });

  it('reset 清空 liveMessage / streamError / activeGenerationId', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: message.done\ndata: {"message_id":"m-1","generation_status":"done"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'gen-1',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.liveMessage).not.toBeNull();
    act(() => {
      result.current.reset();
    });
    expect(result.current.liveMessage).toBeNull();
    expect(result.current.streamError).toBeNull();
    expect(result.current.activeGenerationId).toBeNull();
  });

  it('重新 start 切换 activeGenerationId', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: message.done\ndata: {"message_id":"m-1","generation_status":"done"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'gen-1',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'gen-2',
        sessionId: 'sess-1',
      });
    });
    expect(result.current.activeGenerationId).toBe('gen-2');
    expect(result.current.isStreaming).toBe(true);
    // 等待第二次生成收口,避免测试外悬挂状态更新(act 告警)。
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
  });

  it('created 之前的 delta 被忽略(prev 为 null)', async () => {
    const fetchImpl = streamOf([
      'id: 1\nevent: message.delta\ndata: {"message_id":"m-1","delta":"lost"}\n\n',
      created,
      'id: 3\nevent: message.done\ndata: {"message_id":"m-1","generation_status":"done"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'g',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    // 早到 delta 丢失:正文为空,但生成正常完成
    expect(result.current.liveMessage?.content).toBe('');
    expect(result.current.liveMessage?.generation_status).toBe('done');
  });

  it('done 指向非当前消息时不更新正文状态', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: message.delta\ndata: {"message_id":"m-1","delta":"x"}\n\n',
      'id: 3\nevent: message.done\ndata: {"message_id":"m-other","generation_status":"done"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'g',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.liveMessage?.content).toBe('x');
    expect(result.current.liveMessage?.generation_status).toBe('streaming');
  });

  it('interrupted 指向非当前消息时保留已累积正文', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: message.delta\ndata: {"message_id":"m-1","delta":"y"}\n\n',
      'id: 3\nevent: message.interrupted\ndata: {"message_id":"m-other","partial_content":"z","generation_status":"interrupted"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'g',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.liveMessage?.content).toBe('y');
  });

  it('error 无 message_id 时将当前消息置失败', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: error\ndata: {"code":"boom","message":"x"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'g',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.streamError).toBe('error.boom');
    expect(result.current.liveMessage?.generation_status).toBe('failed');
  });

  it('error 指向非当前消息时不置失败但仍记 streamError', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: error\ndata: {"message_id":"m-other","code":"boom","message":"x"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'g',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.streamError).toBe('error.boom');
    expect(result.current.liveMessage?.generation_status).toBe('streaming');
  });

  it('ping 帧被忽略,不影响生成', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: ping\ndata: {"ts":"2026"}\n\n',
      'id: 3\nevent: message.delta\ndata: {"message_id":"m-1","delta":"ok"}\n\n',
      'id: 4\nevent: message.done\ndata: {"message_id":"m-1","generation_status":"done"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'g',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    expect(result.current.liveMessage?.content).toBe('ok');
  });

  it('无法解析的帧(未知事件/非法 JSON)被跳过', async () => {
    const fetchImpl = streamOf([
      created,
      'id: 2\nevent: message.delta\ndata: not-json\n\n',
      'id: 3\nevent: mystery\ndata: {"a":1}\n\n',
      'id: 4\nevent: message.done\ndata: {"message_id":"m-1","generation_status":"done"}\n\n',
    ]);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'g',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
    // 非法/未知帧被忽略:正文仍为空且正常完成
    expect(result.current.liveMessage?.content).toBe('');
    expect(result.current.liveMessage?.generation_status).toBe('done');
  });
});

describe('useChatStream §5.4 可见性恢复唤醒(单飞续传)', () => {
  it('visible 时 nudge 活动流 → 续传重连并携带 Last-Event-ID', async () => {
    const calls: RecordedCall[] = [];
    const fetchImpl = openStreamFetch(calls, created);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'g',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(calls.length).toBe(1));
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(calls.length).toBe(2));
    // created 帧 id=1 推进游标 → 续传携带 1
    expect((calls[1].init?.headers as Record<string, string>)['Last-Event-ID']).toBe('1');
    result.current.abort();
  });

  it('快速多次 visible 单飞:仅触发一次重连', async () => {
    const calls: RecordedCall[] = [];
    const fetchImpl = openStreamFetch(calls, created);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'g',
        sessionId: 'sess-1',
      });
    });
    await waitFor(() => expect(calls.length).toBe(1));
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
      document.dispatchEvent(new Event('visibilitychange'));
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(calls.length).toBe(2));
    // 多次快速切换仍只重连一次(单飞)
    expect(calls.length).toBe(2);
    result.current.abort();
  });

  it('hidden 时不唤醒(仅在 visible 触发)', async () => {
    const restore = stubVisibility('hidden');
    const calls: RecordedCall[] = [];
    const fetchImpl = openStreamFetch(calls, created);
    const { result } = renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      result.current.start({
        streamUrl: 'http://s/stream',
        generationId: 'g',
        sessionId: 'sess-1',
      });
    });
    // 等待 created 帧处理完(状态更新在 waitFor 的 act 内沉淀,避免 act 告警)
    await waitFor(() => expect(result.current.liveMessage).not.toBeNull());
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    // 仍 hidden → 不重连
    expect(calls.length).toBe(1);
    result.current.abort();
    restore();
  });

  it('无活动流时 visible 不发起请求(handleRef 为空)', async () => {
    const calls: RecordedCall[] = [];
    const fetchImpl = openStreamFetch(calls, created);
    renderHook(() => useChatStream({ getToken: () => null, fetchImpl }));
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(calls.length).toBe(0);
  });
});
