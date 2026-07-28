/**
 * SSE 消费层测试(README §6.8 选项 4:fetch + ReadableStream,自管重连/续传)。
 * 覆盖:帧解析、事件语义解析、鉴权头、终态停止、断流重连(退避 + Last-Event-ID)、close/外部信号。
 */
import { describe, expect, it } from 'vitest';
import { parseSseBlock, parseStreamEvent, shouldAdvanceCursor, streamChatGeneration } from '../sse';
import type { SseFrame } from '../types';

const encoder = new TextEncoder();

/** 由字符串 chunk 序列构造带 ReadableStream 体的 Response。 */
function sseResponse(chunks: readonly string[], status = 200): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return {
    ok: status >= 200 && status < 300,
    status,
    body: stream,
    headers: { get: () => null },
  } as unknown as Response;
}

/** 不关闭的流(保持闲置):监听 abort 信号,中止时以 error 打断挂起的读取(§5.4 nudge 测试)。 */
function sseOpenResponse(chunks: readonly string[], signal?: AbortSignal | null): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      signal?.addEventListener('abort', () => {
        try {
          controller.error(new Error('aborted'));
        } catch {
          /* 已关闭则忽略 */
        }
      });
    },
  });
  return {
    ok: true,
    status: 200,
    body: stream,
    headers: { get: () => null },
  } as unknown as Response;
}

/** 让挂起的流读取微任务沉淀。 */
async function settle(times = 8): Promise<void> {
  for (let i = 0; i < times; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

interface ScheduledTask {
  fn: () => void;
  ms: number;
}

describe('parseSseBlock(线缆帧解析)', () => {
  it('解析 id/event/data', () => {
    const frame = parseSseBlock('id: 5\nevent: message.delta\ndata: {"a":1}');
    expect(frame).toEqual({ id: '5', event: 'message.delta', data: '{"a":1}' });
  });

  it('忽略注释行与冒号后单空格', () => {
    const frame = parseSseBlock(': keep-alive\nevent: ping\ndata: {}');
    expect(frame).toEqual({ id: null, event: 'ping', data: '{}' });
  });

  it('多行 data 以换行拼接', () => {
    const frame = parseSseBlock('event: message.delta\ndata: line1\ndata: line2');
    expect(frame?.data).toBe('line1\nline2');
  });

  it('无 data 且默认事件且无 id → null', () => {
    expect(parseSseBlock(': only comment')).toBeNull();
  });

  it('无冒号字段取空值', () => {
    const frame = parseSseBlock('data');
    expect(frame).toEqual({ id: null, event: 'message', data: '' });
  });
});

describe('parseStreamEvent(事件语义解析,§6.7)', () => {
  const mk = (event: string, data: string): SseFrame => ({ id: '1', event, data });

  it('message.created', () => {
    const ev = parseStreamEvent(
      mk('message.created', '{"message_id":"m","role":"agent","generation_status":"streaming"}'),
    );
    expect(ev).toEqual({
      type: 'message.created',
      message_id: 'm',
      role: 'agent',
      generation_status: 'streaming',
    });
  });

  it('message.delta', () => {
    expect(parseStreamEvent(mk('message.delta', '{"message_id":"m","delta":"hi"}'))).toEqual({
      type: 'message.delta',
      message_id: 'm',
      delta: 'hi',
    });
  });

  it('message.done(completion_tokens 可空)', () => {
    expect(
      parseStreamEvent(
        mk('message.done', '{"message_id":"m","generation_status":"done","completion_tokens":7}'),
      ),
    ).toEqual({
      type: 'message.done',
      message_id: 'm',
      generation_status: 'done',
      completion_tokens: 7,
    });
    expect(
      parseStreamEvent(mk('message.done', '{"message_id":"m","generation_status":"done"}'))
        ?.type === 'message.done',
    ).toBe(true);
  });

  it('message.interrupted', () => {
    expect(
      parseStreamEvent(
        mk(
          'message.interrupted',
          '{"message_id":"m","partial_content":"ab","generation_status":"interrupted"}',
        ),
      ),
    ).toEqual({
      type: 'message.interrupted',
      message_id: 'm',
      partial_content: 'ab',
      generation_status: 'interrupted',
    });
  });

  it('error(message_id 可空)', () => {
    expect(parseStreamEvent(mk('error', '{"message_id":"m","code":"boom","message":"x"}'))).toEqual(
      {
        type: 'error',
        message_id: 'm',
        code: 'boom',
        message: 'x',
      },
    );
    expect(parseStreamEvent(mk('error', '{"code":"boom","message":"x"}'))).toEqual({
      type: 'error',
      message_id: null,
      code: 'boom',
      message: 'x',
    });
  });

  it('ping(合法与非法 JSON)', () => {
    expect(parseStreamEvent(mk('ping', '{"ts":"2026"}'))).toEqual({ type: 'ping', ts: '2026' });
    expect(parseStreamEvent(mk('ping', 'not-json'))).toEqual({ type: 'ping', ts: null });
  });

  it('非法 JSON / 非对象载荷 / 缺字段 / 未知事件 → null', () => {
    expect(parseStreamEvent(mk('message.delta', 'oops'))).toBeNull();
    expect(parseStreamEvent(mk('message.delta', '"scalar"'))).toBeNull();
    expect(parseStreamEvent(mk('message.delta', '{"message_id":"m"}'))).toBeNull();
    expect(parseStreamEvent(mk('message.unknown', '{"a":1}'))).toBeNull();
  });
});

describe('streamChatGeneration(fetch 流 + 自管重连,§6.8)', () => {
  it('携带 Authorization 与 Accept 头消费帧,终态后停止', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchImpl = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return sseResponse([
        'id: 1\nevent: message.created\ndata: {"message_id":"m","role":"agent","generation_status":"streaming"}\n\n',
        'id: 2\nevent: message.delta\ndata: {"message_id":"m","delta":"hi"}\n\n',
        'id: 3\nevent: message.done\ndata: {"message_id":"m","generation_status":"done"}\n\n',
      ]);
    }) as typeof fetch;
    const frames: SseFrame[] = [];
    const scheduled: ScheduledTask[] = [];
    streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => 'tok',
      onFrame: (frame) => frames.push(frame),
      fetchImpl,
      schedule: (fn, ms) => scheduled.push({ fn, ms }),
      random: () => 0.5,
    });
    await settle();
    const headers = calls[0].init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer tok');
    expect(headers.Accept).toBe('text/event-stream');
    expect(frames.map((frame) => frame.event)).toEqual([
      'message.created',
      'message.delta',
      'message.done',
    ]);
    // 终态后不重连
    expect(scheduled.length).toBe(0);
  });

  it('断流(无终态)→ 退避重连并携带 Last-Event-ID', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    let callIndex = 0;
    const responses = [
      sseResponse(['id: 7\nevent: message.delta\ndata: {"message_id":"m","delta":"a"}\n\n']),
      sseResponse([
        'id: 8\nevent: message.done\ndata: {"message_id":"m","generation_status":"done"}\n\n',
      ]),
    ];
    const fetchImpl = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return responses[callIndex++];
    }) as typeof fetch;
    const frames: SseFrame[] = [];
    const scheduled: ScheduledTask[] = [];
    streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      onFrame: (frame) => frames.push(frame),
      fetchImpl,
      schedule: (fn, ms) => scheduled.push({ fn, ms }),
      random: () => 0.5,
      baseDelayMs: 1000,
      maxDelayMs: 30000,
    });
    await settle();
    expect(frames.map((frame) => frame.event)).toEqual(['message.delta']);
    // 第一段流结束无终态 → 排一次重连,退避 base*2^0*1 = 1000ms
    expect(scheduled.length).toBe(1);
    expect(scheduled[0].ms).toBe(1000);
    // 触发重连
    scheduled[0].fn();
    await settle();
    expect(calls.length).toBe(2);
    // 续传:第二次请求携带 Last-Event-ID = 上次见到的帧 id
    expect((calls[1].init?.headers as Record<string, string>)['Last-Event-ID']).toBe('7');
    expect(frames.map((frame) => frame.event)).toEqual(['message.delta', 'message.done']);
    // 终态后不再排重连
    expect(scheduled.length).toBe(1);
  });

  it('初始 lastEventId 作为首请求 Last-Event-ID', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchImpl = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return sseResponse([
        'id: 9\nevent: message.done\ndata: {"message_id":"m","generation_status":"done"}\n\n',
      ]);
    }) as typeof fetch;
    streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      lastEventId: '3',
      onFrame: () => undefined,
      fetchImpl,
      schedule: () => undefined,
    });
    await settle();
    expect((calls[0].init?.headers as Record<string, string>)['Last-Event-ID']).toBe('3');
  });

  it('非 2xx 响应触发退避重连', async () => {
    const scheduled: ScheduledTask[] = [];
    const fetchImpl = (async () => sseResponse([], 500)) as typeof fetch;
    streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      onFrame: () => undefined,
      fetchImpl,
      schedule: (fn, ms) => scheduled.push({ fn, ms }),
      random: () => 0.5,
    });
    await settle();
    expect(scheduled.length).toBe(1);
  });

  it('fetch reject 触发退避重连', async () => {
    const scheduled: ScheduledTask[] = [];
    const fetchImpl = (async () => {
      throw new Error('network');
    }) as typeof fetch;
    streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      onFrame: () => undefined,
      fetchImpl,
      schedule: (fn, ms) => scheduled.push({ fn, ms }),
      random: () => 0.5,
    });
    await settle();
    expect(scheduled.length).toBe(1);
  });

  it('退避上限封顶(指数增长不超过 maxDelay)', async () => {
    // attempt 大到 2^n 远超上限:base=1000,attempt=10 → min(30000, 1024000)=30000
    const scheduled: ScheduledTask[] = [];
    let callIndex = 0;
    const fetchImpl = (async () => {
      callIndex += 1;
      return sseResponse([], 500); // 持续失败 → 反复重连
    }) as typeof fetch;
    streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      onFrame: () => undefined,
      fetchImpl,
      schedule: (fn, ms) => scheduled.push({ fn, ms }),
      random: () => 0.5,
      baseDelayMs: 1000,
      maxDelayMs: 30000,
    });
    await settle();
    // 连续触发若干次重连,验证延迟封顶 30000
    for (let i = 0; i < 8; i += 1) {
      const task = scheduled[scheduled.length - 1];
      if (task) task.fn();
      await settle();
    }
    for (const task of scheduled) expect(task.ms).toBeLessThanOrEqual(30000);
    expect(scheduled.some((task) => task.ms === 30000)).toBe(true);
    expect(callIndex).toBeGreaterThan(2);
  });

  it('close() 幂等终止,阻止后续重连', async () => {
    const scheduled: ScheduledTask[] = [];
    const fetchImpl = (async () => sseResponse([], 500)) as typeof fetch;
    const handle = streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      onFrame: () => undefined,
      fetchImpl,
      schedule: (fn, ms) => scheduled.push({ fn, ms }),
      random: () => 0.5,
    });
    await settle();
    handle.close();
    handle.close(); // 幂等
    const before = scheduled.length;
    const task = scheduled[scheduled.length - 1];
    if (task) task.fn(); // close 后定时器回调应为空操作
    await settle();
    expect(scheduled.length).toBe(before);
  });

  it('预中止外部信号 → 不发起请求', async () => {
    const controller = new AbortController();
    controller.abort();
    let called = 0;
    const fetchImpl = (async () => {
      called += 1;
      return sseResponse([]);
    }) as typeof fetch;
    streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      signal: controller.signal,
      onFrame: () => undefined,
      fetchImpl,
      schedule: () => undefined,
    });
    await settle();
    expect(called).toBe(0);
  });

  it('外部信号中途 abort → 停止', async () => {
    const controller = new AbortController();
    const scheduled: ScheduledTask[] = [];
    const fetchImpl = (async () => sseResponse([], 500)) as typeof fetch;
    streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      signal: controller.signal,
      onFrame: () => undefined,
      fetchImpl,
      schedule: (fn, ms) => scheduled.push({ fn, ms }),
      random: () => 0.5,
    });
    await settle();
    controller.abort();
    const before = scheduled.length;
    const task = scheduled[scheduled.length - 1];
    if (task) task.fn();
    await settle();
    expect(scheduled.length).toBe(before);
  });
});

describe('shouldAdvanceCursor(H4:续传游标只由真实数据帧推进)', () => {
  const frame = (event: string, id: string | null): SseFrame => ({ id, event, data: '{}' });

  it('真实数据帧(非 ping + 非零数字 id)推进游标', () => {
    expect(shouldAdvanceCursor(frame('message.delta', '5'))).toBe(true);
    expect(shouldAdvanceCursor(frame('message.done', '12'))).toBe(true);
  });

  it('ping 帧即使带数字 id 也不推进', () => {
    expect(shouldAdvanceCursor(frame('ping', '6'))).toBe(false);
    expect(shouldAdvanceCursor(frame('ping', '0'))).toBe(false);
  });

  it('id 为 null / 0 / 非数字 的帧不推进', () => {
    expect(shouldAdvanceCursor(frame('message.delta', null))).toBe(false);
    expect(shouldAdvanceCursor(frame('message.delta', '0'))).toBe(false);
    expect(shouldAdvanceCursor(frame('message.delta', 'abc'))).toBe(false);
    expect(shouldAdvanceCursor(frame('message.delta', '1.5'))).toBe(false);
  });
});

describe('H4 续传水位:interrupt 后 Last-Event-ID 取最后真实数据帧(忽略 ping)', () => {
  it('[delta id=5, ping id=0, delta id=6] 断流后重连携带 6 而非 0', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    let callIndex = 0;
    const responses = [
      // 第一段:delta5 → ping0 → delta6,随后流被切断(无终态)
      sseResponse([
        'id: 5\nevent: message.delta\ndata: {"message_id":"m","delta":"a"}\n\n',
        'id: 0\nevent: ping\ndata: {"ts":"2026"}\n\n',
        'id: 6\nevent: message.delta\ndata: {"message_id":"m","delta":"b"}\n\n',
      ]),
      sseResponse([
        'id: 7\nevent: message.done\ndata: {"message_id":"m","generation_status":"done"}\n\n',
      ]),
    ];
    const fetchImpl = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return responses[callIndex++];
    }) as typeof fetch;
    const scheduled: ScheduledTask[] = [];
    streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      onFrame: () => undefined,
      fetchImpl,
      schedule: (fn, ms) => scheduled.push({ fn, ms }),
      random: () => 0.5,
    });
    await settle();
    // 第一段无终态 → 排一次退避重连
    expect(scheduled.length).toBe(1);
    scheduled[0].fn();
    await settle();
    expect(calls.length).toBe(2);
    // ping(id=0) 不推进游标 → 续传水位为最后真实帧 6
    expect((calls[1].init?.headers as Record<string, string>)['Last-Event-ID']).toBe('6');
  });
});

describe('§5.4 nudge:可见性恢复单飞唤醒(立即续传 + Last-Event-ID)', () => {
  it('闲置流被 nudge 立即续传;快速多次 nudge 单飞;重连建立后可再次唤醒', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchImpl = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      // 每次返回不关闭的闲置流(仅一帧 delta id=5),并监听本次连接的 abort 信号
      return sseOpenResponse(
        ['id: 5\nevent: message.delta\ndata: {"message_id":"m","delta":"a"}\n\n'],
        init?.signal,
      );
    }) as typeof fetch;
    const scheduled: ScheduledTask[] = [];
    const handle = streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      onFrame: () => undefined,
      fetchImpl,
      schedule: (fn, ms) => scheduled.push({ fn, ms }),
      random: () => 0.5,
    });
    await settle();
    expect(calls.length).toBe(1);

    // 快速连续两次唤醒(多标签/抖动)→ 单飞,仅触发一次重连
    handle.nudge();
    handle.nudge();
    await settle();
    expect(calls.length).toBe(2);
    // 立即续传(不退避):不经过 schedule 排程
    expect(scheduled.length).toBe(0);
    expect((calls[1].init?.headers as Record<string, string>)['Last-Event-ID']).toBe('5');

    // 重连建立后单飞周期结束,可再次唤醒
    handle.nudge();
    await settle();
    expect(calls.length).toBe(3);
    handle.close();
  });

  it('close 后 nudge 为空操作(不再重连)', async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchImpl = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return sseOpenResponse([], init?.signal);
    }) as typeof fetch;
    const handle = streamChatGeneration({
      url: 'http://s/stream',
      getToken: () => null,
      onFrame: () => undefined,
      fetchImpl,
      schedule: () => undefined,
    });
    await settle();
    handle.close();
    handle.nudge();
    await settle();
    expect(calls.length).toBe(1);
  });
});
