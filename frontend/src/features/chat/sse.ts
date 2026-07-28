/**
 * 聊天流式输出消费层(README §6.8 唯一权威 / chat-session.md §3.3)。
 *
 * **本实现为 fetch + ReadableStream 手工解析 SSE,不是原生 EventSource**(§6.8 选项 4):
 * 鉴权仅 Authorization 头(EventSource 无法发自定义头),故断点续传与重连退避全部自管 ——
 * 携带 `Last-Event-ID: <last seen frame id>` 重连,指数退避 1s→30s 上限 + ±20% 抖动。
 * 命中终态事件(message.done / message.interrupted / error)即停止,不再重连;
 * 流被服务端关闭而无终态(网络抖动)则按退避重连。close() 主动终止,取消挂起重连。
 *
 * 解析:SSE 帧形如 `id: N\nevent: <name>\ndata: <json>\n\n`;按空行切帧,跨 chunk 缓冲,
 * `\r\n` 归一为 `\n`,`:` 起始行为注释。data 多行以 `\n` 拼接(标准 SSE 语义)。
 */
import type { ChatRole, GenerationStatus, SseFrame, StreamEvent } from './types';
import { TERMINAL_STREAM_EVENTS } from './types';

const DEFAULT_BASE_DELAY_MS = 1000;
const DEFAULT_MAX_DELAY_MS = 30_000;
const JITTER_RATIO = 0.2;
const SSE_FRAME_SEPARATOR = '\n\n';
const DEFAULT_EVENT_NAME = 'message';

export interface StreamChatGenerationOptions {
  /** 后端返回的 stream_url(绝对或同源相对路径) */
  url: string;
  getToken: () => string | null;
  /** 续传起点:上次见到的帧 id;缺省从头。 */
  lastEventId?: string | null;
  /** 每个完整帧回调(线缆形态;UI 语义解析见 parseStreamEvent) */
  onFrame: (frame: SseFrame) => void;
  /** 外部终止信号(组件卸载 / 用户离开) */
  signal?: AbortSignal;
  /** 测试可注入 fetch 实现;缺省全局 fetch。 */
  fetchImpl?: typeof fetch;
  /** 测试可注入定时器;缺省 setTimeout。 */
  schedule?: (fn: () => void, ms: number) => void;
  /** 测试可注入随机源(抖动确定性);缺省 Math.random。 */
  random?: () => number;
  baseDelayMs?: number;
  maxDelayMs?: number;
}

export interface StreamHandle {
  /** 主动终止:中止在途请求并取消挂起重连,幂等。 */
  close: () => void;
  /**
   * 可见性恢复时的单次唤醒(§5.4):流仍活动但疑似闲置 → 立即续传重连一次。
   * 单飞:一次唤醒至重连建立期间,重复调用(多标签/快速切换)不产生额外重连。
   */
  nudge: () => void;
}

/** 裸 fetch 经箭头转发,规避 window.fetch 以错误接收者调用的 "Illegal invocation"。 */
const defaultFetchImpl: typeof fetch = (...args) => fetch(...args);

/**
 * 解析单个 SSE 帧块(已按空行切出,不含分隔符)。
 * 无 data 且事件为默认 message 且无 id → 返回 null(无意义块)。
 */
export function parseSseBlock(block: string): SseFrame | null {
  const lines = block.split('\n');
  let id: string | null = null;
  let event = DEFAULT_EVENT_NAME;
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line === '' || line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'id') id = value;
    else if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
  }
  if (dataLines.length === 0 && id === null && event === DEFAULT_EVENT_NAME) return null;
  return { id, event, data: dataLines.join('\n') };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function stringField(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === 'string' ? value : null;
}

/**
 * 将线缆帧解析为 UI 语义事件(§6.7 流内事件注册表)。
 * 非法 JSON / 未知事件 / 缺字段 → null(容错跳过,连接保持)。
 */
export function parseStreamEvent(frame: SseFrame): StreamEvent | null {
  if (frame.event === 'ping') {
    let ts: string | null = null;
    try {
      const payload = JSON.parse(frame.data) as unknown;
      if (isRecord(payload)) ts = stringField(payload, 'ts');
    } catch {
      ts = null;
    }
    return { type: 'ping', ts };
  }

  let payload: unknown;
  try {
    payload = JSON.parse(frame.data) as unknown;
  } catch {
    return null;
  }
  if (!isRecord(payload)) return null;

  switch (frame.event) {
    case 'message.created': {
      const messageId = stringField(payload, 'message_id');
      const role = stringField(payload, 'role');
      const status = stringField(payload, 'generation_status');
      if (messageId === null || role === null || status === null) return null;
      return {
        type: 'message.created',
        message_id: messageId,
        role: role as ChatRole,
        generation_status: status as GenerationStatus,
      };
    }
    case 'message.delta': {
      const messageId = stringField(payload, 'message_id');
      const delta = stringField(payload, 'delta');
      if (messageId === null || delta === null) return null;
      return { type: 'message.delta', message_id: messageId, delta };
    }
    case 'message.done': {
      const messageId = stringField(payload, 'message_id');
      const status = stringField(payload, 'generation_status');
      if (messageId === null || status === null) return null;
      const tokens = payload.completion_tokens;
      return {
        type: 'message.done',
        message_id: messageId,
        generation_status: status as GenerationStatus,
        completion_tokens: typeof tokens === 'number' ? tokens : null,
      };
    }
    case 'message.interrupted': {
      const messageId = stringField(payload, 'message_id');
      const partial = stringField(payload, 'partial_content');
      const status = stringField(payload, 'generation_status');
      if (messageId === null || partial === null || status === null) return null;
      return {
        type: 'message.interrupted',
        message_id: messageId,
        partial_content: partial,
        generation_status: status as GenerationStatus,
      };
    }
    case 'error': {
      const code = stringField(payload, 'code');
      const message = stringField(payload, 'message');
      if (code === null || message === null) return null;
      return {
        type: 'error',
        message_id: stringField(payload, 'message_id'),
        code,
        message,
      };
    }
    default:
      return null;
  }
}

/** 合法续传游标帧 id:非零纯数字。 */
const NUMERIC_FRAME_ID = /^[0-9]+$/;

/**
 * H4:续传游标(Last-Event-ID)只能由「真实数据帧」推进。心跳 ping 帧、以及
 * id 为 '0' / 非纯数字的帧一律不更新游标 —— 否则断线重连会携带错误水位,
 * 漏放或重放数据。仅 event!=='ping' 且 id 为非零纯数字的帧才推进。
 */
export function shouldAdvanceCursor(frame: SseFrame): boolean {
  if (frame.event === 'ping') return false;
  if (frame.id === null) return false;
  if (frame.id === '0') return false;
  return NUMERIC_FRAME_ID.test(frame.id);
}

/**
 * 启动一次生成流消费。返回 close() 终止句柄。
 * 重连/退避/续传全部内聚于此(非原生 EventSource,§6.8 选项 4)。
 */
export function streamChatGeneration(opts: StreamChatGenerationOptions): StreamHandle {
  const fetchImpl = opts.fetchImpl ?? defaultFetchImpl;
  const schedule =
    opts.schedule ??
    ((fn: () => void, ms: number): void => {
      setTimeout(fn, ms);
    });
  const random = opts.random ?? Math.random;
  const baseDelayMs = opts.baseDelayMs ?? DEFAULT_BASE_DELAY_MS;
  const maxDelayMs = opts.maxDelayMs ?? DEFAULT_MAX_DELAY_MS;

  let closed = false;
  let lastId = opts.lastEventId ?? null;
  let attempt = 0;
  // 每次连接独立一个控制器:nudge 需中止在途读取并立即重连,close 需中止当前连接。
  let controller = new AbortController();
  // §5.4 单飞唤醒:nudgeRequested 令下一次断开走「立即续传」而非退避;
  // nudgeInFlight 在一次唤醒至重连建立期间屏蔽重复 nudge(多标签/快速切换)。
  let nudgeRequested = false;
  let nudgeInFlight = false;

  const onExternalAbort = (): void => {
    close();
  };
  if (opts.signal !== undefined) {
    if (opts.signal.aborted) {
      closed = true;
    } else {
      opts.signal.addEventListener('abort', onExternalAbort, { once: true });
    }
  }

  function close(): void {
    if (closed) return;
    closed = true;
    if (opts.signal !== undefined) opts.signal.removeEventListener('abort', onExternalAbort);
    controller.abort();
  }

  function backoffDelay(retryAttempt: number): number {
    const base = Math.min(maxDelayMs, baseDelayMs * 2 ** retryAttempt);
    const jitter = 1 + (random() * 2 - 1) * JITTER_RATIO;
    return Math.round(base * jitter);
  }

  function scheduleReconnect(): void {
    if (closed) return;
    const delay = backoffDelay(attempt);
    attempt += 1;
    schedule(() => {
      if (!closed) void connect();
    }, delay);
  }

  /**
   * 断开后的续接:nudge 触发的断开走「立即续传」(不退避,不丢游标);
   * 其余断开(网络抖动/流被切断)走指数退避重连。
   */
  function reconnectAfterDrop(): void {
    if (closed) return;
    if (nudgeRequested) {
      nudgeRequested = false;
      void connect();
      return;
    }
    scheduleReconnect();
  }

  /**
   * §5.4 可见性恢复唤醒:流活动但疑似闲置时,中止当前读取并以 Last-Event-ID 立即续传。
   * 单飞:一次唤醒周期(至重连建立)内重复调用直接返回,避免多标签/快速切换风暴。
   */
  function nudge(): void {
    if (closed || nudgeInFlight) return;
    nudgeInFlight = true;
    nudgeRequested = true;
    attempt = 0; // 立即续传,跳过退避
    controller.abort(); // 中止在途读取 → consumeBody 抛错 → reconnectAfterDrop 立即重连
  }

  async function consumeBody(body: ReadableStream<Uint8Array>): Promise<boolean> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let terminated = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, '\n');
      let separator: number;
      while ((separator = buffer.indexOf(SSE_FRAME_SEPARATOR)) !== -1) {
        const block = buffer.slice(0, separator);
        buffer = buffer.slice(separator + SSE_FRAME_SEPARATOR.length);
        const frame = parseSseBlock(block);
        if (frame === null) continue;
        // H4:仅真实数据帧推进续传游标(ping/'0'/非数字帧不更新,见 shouldAdvanceCursor)。
        if (shouldAdvanceCursor(frame)) lastId = frame.id as string;
        opts.onFrame(frame);
        if (TERMINAL_STREAM_EVENTS.has(frame.event)) terminated = true;
      }
      if (terminated) break;
    }
    return terminated;
  }

  async function connect(): Promise<void> {
    if (closed) return;
    // 每次连接换新控制器:nudge/close 中止的是「当前」连接,旧控制器已作废。
    controller = new AbortController();
    const headers: Record<string, string> = { Accept: 'text/event-stream' };
    const token = opts.getToken();
    if (token !== null) headers.Authorization = 'Bearer ' + token;
    if (lastId !== null) headers['Last-Event-ID'] = lastId;

    let response: Response;
    try {
      response = await fetchImpl(opts.url, { headers, signal: controller.signal });
    } catch {
      reconnectAfterDrop();
      return;
    }

    if (!response.ok || response.body === null) {
      reconnectAfterDrop();
      return;
    }

    // 连接建立成功:退避计数清零(下一次断开从 base 重新计),并放行下一次唤醒。
    attempt = 0;
    nudgeInFlight = false;
    let terminated = false;
    try {
      terminated = await consumeBody(response.body);
    } catch {
      // 读取中断(网络抖动 / nudge 中止 / abort):非主动关闭则续接(退避或立即续传)。
      reconnectAfterDrop();
      return;
    }

    if (closed) return;
    if (terminated) {
      close();
      return;
    }
    // 流正常结束但无终态帧:视为连接被切断,退避重连(续传 Last-Event-ID)。
    scheduleReconnect();
  }

  if (!closed) void connect();

  return { close, nudge };
}
