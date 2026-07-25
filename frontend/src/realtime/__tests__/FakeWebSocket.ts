/**
 * 测试用 WebSocket 替身:记录 url/protocols、捕获 send 的帧,
 * 由测试手动触发 open()/message()/emitClose()/emitError()。
 * 经 `as unknown as typeof WebSocket` 注入 RealtimeClient。
 */
export interface MessageEventLike {
  data: unknown;
}

export class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readonly protocols: string | string[];
  readyState: number = FakeWebSocket.CONNECTING;
  sent: string[] = [];
  closeCalled = false;

  onopen: ((ev?: unknown) => void) | null = null;
  onmessage: ((ev: MessageEventLike) => void) | null = null;
  onclose: ((ev?: unknown) => void) | null = null;
  onerror: ((ev?: unknown) => void) | null = null;

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols ?? [];
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.closeCalled = true;
    this.readyState = FakeWebSocket.CLOSED;
  }

  /** 测试触发:连接建立 */
  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.({});
  }

  /** 测试触发:收到对象帧(自动 JSON 序列化) */
  message(payload: unknown): void {
    this.onmessage?.({ data: typeof payload === 'string' ? payload : JSON.stringify(payload) });
  }

  /** 测试触发:收到原始字符串(可非法 JSON) */
  raw(data: unknown): void {
    this.onmessage?.({ data });
  }

  /** 测试触发:连接关闭 */
  emitClose(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({});
  }

  /** 测试触发:连接错误 */
  emitError(): void {
    this.onerror?.({});
  }

  /** 解析所有已发送帧为对象 */
  sentOps(): Array<Record<string, unknown>> {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }

  static get last(): FakeWebSocket {
    const instance = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    if (!instance) throw new Error('no FakeWebSocket instance created');
    return instance;
  }

  static reset(): void {
    FakeWebSocket.instances = [];
  }
}

/** 将 FakeWebSocket 作为 WebSocket 构造器注入 */
export const FakeWebSocketImpl = FakeWebSocket as unknown as typeof WebSocket;
