/**
 * SSE 编排流消费测试(squad.md §3.2 / §6.8):帧解析(event/id/data、注释跳过、多 data
 * 合并)+ fetch 流式读取(认证头、Last-Event-ID 续传、非 2xx / 无主体抛错降级)。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { connectTaskStream, parseSseFrame } from '../stream';
import type { TaskStreamFrame } from '../stream';

const encoder = new TextEncoder();

/** 构造一个主体可读流的 Response 桩:依次吐出给定 SSE 文本块后正常结束。 */
function streamResponse(chunks: readonly string[], status = 200): Response {
  const encoded = chunks.map((chunk) => encoder.encode(chunk));
  let cursor = 0;
  const reader = {
    read: async (): Promise<{ done: boolean; value?: Uint8Array }> => {
      if (cursor < encoded.length) {
        const value = encoded[cursor];
        cursor += 1;
        return { done: false, value };
      }
      return { done: true };
    },
  };
  return {
    ok: status >= 200 && status < 300,
    status,
    body: { getReader: () => reader },
    headers: { get: () => null },
  } as unknown as Response;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('parseSseFrame', () => {
  it('parses event, id and data fields', () => {
    const frame = parseSseFrame('event: task.status\nid: 5\ndata: {"status":"done"}');
    expect(frame).toEqual({ event: 'task.status', id: 5, data: '{"status":"done"}' });
  });

  it('joins multiple data lines with a newline', () => {
    const frame = parseSseFrame('event: plan.submitted\ndata: line1\ndata: line2');
    expect(frame?.data).toBe('line1\nline2');
  });

  it('ignores comment / keepalive lines and returns null when nothing actionable', () => {
    expect(parseSseFrame(': ping')).toBeNull();
  });

  it('defaults the event name to message when only data is present', () => {
    const frame = parseSseFrame('data: hello');
    expect(frame).toEqual({ event: 'message', id: null, data: 'hello' });
  });
});

describe('connectTaskStream', () => {
  it('emits each persisted frame and returns the last event id', async () => {
    const stub = vi.fn(async () =>
      streamResponse([
        'event: task.status\nid: 1\ndata: {"a":1}\n\n',
        'event: subtask.created\nid: 2\ndata: {"b":2}\n\n: heartbeat\n\n',
      ]),
    );
    vi.stubGlobal('fetch', stub);
    const frames: TaskStreamFrame[] = [];
    const lastId = await connectTaskStream({
      url: 'http://api/stream',
      getToken: () => null,
      onFrame: (frame) => frames.push(frame),
    });
    expect(frames).toEqual([
      { event: 'task.status', id: 1, data: '{"a":1}' },
      { event: 'subtask.created', id: 2, data: '{"b":2}' },
    ]);
    expect(lastId).toBe(2);
  });

  it('sends the Bearer token and Last-Event-ID headers', async () => {
    const stub = vi.fn(async (_url: string, _init?: RequestInit) =>
      streamResponse(['event: task.status\nid: 9\ndata: {}\n\n']),
    );
    vi.stubGlobal('fetch', stub);
    await connectTaskStream({
      url: 'http://api/stream',
      getToken: () => 'tok-123',
      lastEventId: 8,
      onFrame: () => undefined,
    });
    const headers = stub.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer tok-123');
    expect(headers['Last-Event-ID']).toBe('8');
  });

  it('throws on a non-2xx response so the caller can degrade to polling', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => streamResponse([], 500)));
    await expect(
      connectTaskStream({ url: 'http://api/stream', getToken: () => null, onFrame: () => undefined }),
    ).rejects.toThrow(/500/);
  });

  it('throws when the response has no readable body', async () => {
    const noBody = { ok: true, status: 200, body: undefined, headers: { get: () => null } } as unknown as Response;
    vi.stubGlobal('fetch', vi.fn(async () => noBody));
    await expect(
      connectTaskStream({ url: 'http://api/stream', getToken: () => null, onFrame: () => undefined }),
    ).rejects.toThrow(/unavailable/);
  });
});
