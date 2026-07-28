/**
 * 小队任务编排 SSE 流消费(squad.md §3.2 / §3.5 / §6.8)。
 *
 * EventSource 无法携带 Authorization 头,而后端仅以 Bearer 头鉴权,故此处以
 * `fetch` 流式读取 `text/event-stream` 主体并手动带凭证(认证兼容)。每个持久化
 * 帧带 `id`(频道内 seq);重连经 `Last-Event-ID` 让服务端重放 seq > last 的帧
 * (无丢失 / 无重复,§6.8)。仅消费五类编排事件并触发上层重取,不渲染原始帧。
 */
import { AUTH_HEADER, bearerHeader } from '../../api';

/** 触发重取的五类编排事件(§6.8)。 */
export const TASK_STREAM_EVENTS: ReadonlySet<string> = new Set([
  'task.status',
  'subtask.created',
  'subtask.assigned',
  'plan.submitted',
  'task.aggregated',
]);

export interface TaskStreamFrame {
  readonly event: string;
  readonly id: number | null;
  readonly data: string;
}

/**
 * 解析单个 SSE 帧块(以空行分隔后的一段)。忽略注释行(以 `:` 开头,含心跳);
 * 无 data 且无显式 event 的块返回 null(如纯 keepalive)。多 data 行以 `\n` 连接。
 */
export function parseSseFrame(raw: string): TaskStreamFrame | null {
  let event = 'message';
  let id: number | null = null;
  const dataLines: string[] = [];
  for (const line of raw.split('\n')) {
    if (line === '' || line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    const field = colon >= 0 ? line.slice(0, colon) : line;
    let value = colon >= 0 ? line.slice(colon + 1) : '';
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') {
      event = value;
    } else if (field === 'data') {
      dataLines.push(value);
    } else if (field === 'id') {
      const parsed = Number.parseInt(value, 10);
      if (!Number.isNaN(parsed)) id = parsed;
    }
  }
  if (dataLines.length === 0 && event === 'message') return null;
  return { event, id, data: dataLines.join('\n') };
}

export interface ConnectTaskStreamOptions {
  readonly url: string;
  readonly getToken: () => string | null;
  /** 续传水位:作为 `Last-Event-ID` 头上行(>0 时),服务端重放其后帧。 */
  readonly lastEventId?: number;
  readonly signal?: AbortSignal;
  readonly onFrame: (frame: TaskStreamFrame) => void;
}

/**
 * 打开并消费一条任务流,返回最后见到的事件 id(供重连续传)。
 * HTTP 非 2xx 或无主体时抛错(由调用方降级到轮询);主体正常读完(服务端收尾)
 * 则正常返回。 AbortSignal 触发时读循环经底层 reject 中断。
 */
export async function connectTaskStream(opts: ConnectTaskStreamOptions): Promise<number> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  const token = opts.getToken();
  if (token !== null) headers[AUTH_HEADER] = bearerHeader(token);
  const lastEventId = opts.lastEventId ?? 0;
  if (lastEventId > 0) headers['Last-Event-ID'] = String(lastEventId);

  const response = await fetch(opts.url, { headers, signal: opts.signal });
  if (!response.ok) {
    throw new Error(`task stream HTTP ${response.status}`);
  }
  const body = response.body as ReadableStream<Uint8Array> | null | undefined;
  if (body === null || body === undefined) {
    throw new Error('task stream body unavailable');
  }

  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let lastId = lastEventId;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separator = buffer.indexOf('\n\n');
    while (separator >= 0) {
      const rawFrame = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      const frame = parseSseFrame(rawFrame);
      if (frame !== null) {
        if (frame.id !== null) lastId = frame.id;
        opts.onFrame(frame);
      }
      separator = buffer.indexOf('\n\n');
    }
  }
  return lastId;
}
