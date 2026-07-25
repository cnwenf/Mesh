/**
 * 测试用 fetch 桩:构造最小 Response 形状,并记录每次调用的 url/init。
 * 仅用于 vitest;不进入覆盖率统计(__tests__ 已被排除)。
 */

export interface FakeResponseInit {
  status?: number;
  /** JSON 可序列化响应体;与 rawText 同为 undefined 时返回空体 */
  body?: unknown;
  /** 原始响应文本(覆盖 body) */
  rawText?: string;
  headers?: Record<string, string>;
}

export function fakeResponse(init: FakeResponseInit = {}): Response {
  const status = init.status ?? 200;
  const text = init.rawText ?? (init.body === undefined ? '' : JSON.stringify(init.body));
  const headers = init.headers ?? {};
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
    headers: {
      get: (name: string) => headers[name] ?? null,
    },
  } as unknown as Response;
}

export interface RecordedCall {
  url: string;
  init?: RequestInit;
}

export interface FetchStub {
  fetchImpl: typeof fetch;
  calls: RecordedCall[];
}

/** 顺序返回给定响应;调用次数超出时复用最后一个。 */
export function stubFetch(...responses: Response[]): FetchStub {
  const calls: RecordedCall[] = [];
  let index = 0;
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    const response = responses[Math.min(index, responses.length - 1)];
    index += 1;
    return response;
  }) as typeof fetch;
  return { fetchImpl, calls };
}

/** fetch 直接 reject(模拟网络失败)。 */
export function failingFetch(): typeof fetch {
  return (async () => {
    throw new Error('boom');
  }) as typeof fetch;
}

/** 读取某次调用记录的请求头(普通对象)。 */
export function headersOf(call: RecordedCall): Record<string, string> {
  return (call.init?.headers ?? {}) as Record<string, string>;
}
