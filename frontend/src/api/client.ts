/**
 * Mesh API 客户端契约层 — 权威:docs/specs/README.md §6.14。
 * - 三类成功包络:单对象取 `data`;列表 `{data,next_cursor}` 原样;分组 `{groups,next_cursor}` 整体游标。
 * - 鉴权:`Authorization: Bearer <token>`(token 为空则不带头,便于登录前调用)。
 * - 幂等写(§6.5):POST/PUT/PATCH/DELETE 自动携带 `Idempotency-Key`(可显式覆盖);GET 不携带。
 * - 乐观并发:`ifMatch` → `If-Match` 头(冲突 409,见 optimistic.ts)。
 * - 错误一律归一为 MeshApiError;429 解析 `Retry-After`。
 */
import type { GroupedEnvelope, ListEnvelope } from '../types/envelopes';
import { isErrorEnvelope } from '../types/envelopes';
import { MeshApiError } from './errors';
import { AUTH_HEADER, bearerHeader } from './tokenStore';
import { isAuthExemptPath } from './unauthorized';

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

/** 动作类方法:默认自动携带 Idempotency-Key(§6.5) */
const IDEMPOTENT_METHODS: ReadonlySet<HttpMethod> = new Set<HttpMethod>([
  'POST',
  'PUT',
  'PATCH',
  'DELETE',
]);

const CONTENT_TYPE_HEADER = 'Content-Type';
const CONTENT_TYPE_JSON = 'application/json';
const IF_MATCH_HEADER = 'If-Match';
const IDEMPOTENCY_KEY_HEADER = 'Idempotency-Key';
const RETRY_AFTER_HEADER = 'Retry-After';
const HTTP_TOO_MANY_REQUESTS = 429;
const HTTP_UNAUTHORIZED = 401;
const HTTP_NO_CONTENT = 204;

export interface ClientOptions {
  baseUrl: string;
  getToken: () => string | null;
  /** 测试可注入 fetch 实现;缺省使用全局 fetch */
  fetchImpl?: typeof fetch;
  /**
   * 非鉴权豁免端点收到 401(token 失效/未登录)时触发(MES-106 全局兜底:
   * 清 token + 跳登录页,见 unauthorized.ts)。鉴权豁免端点(登录/注册/MFA
   * 验证等)的 401/4xx 属业务错误,就地呈现具名文案,不触发此回调。
   * 回调先于 MeshApiError 抛出同步执行;请求 Promise 仍照常 reject。
   */
  onUnauthorized?: () => void;
}

export interface RequestOptions {
  /** 查询参数;值为 undefined 的键被跳过 */
  query?: Record<string, string | number | boolean | undefined>;
  /** 请求体;非 undefined 时 JSON 序列化并置 Content-Type: application/json */
  body?: unknown;
  /** 乐观并发:If-Match: <updated_at>(§6.14) */
  ifMatch?: string;
  /** 显式 Idempotency-Key;缺省时动作类方法自动生成(§6.5) */
  idempotencyKey?: string;
  /** 额外请求头(不会被就地修改) */
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/** baseUrl + path + querystring;path 可带或不带前导 `/`;跳过 undefined 查询值。 */
function buildUrl(baseUrl: string, path: string, query?: RequestOptions['query']): string {
  const base = baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  let url = `${base}${normalizedPath}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) {
        params.append(key, String(value));
      }
    }
    const qs = params.toString();
    if (qs !== '') {
      url = `${url}?${qs}`;
    }
  }
  return url;
}

/** 解析 `Retry-After`:整数秒 → number;HTTP-date → 距今秒数(向下取整,最小 0);缺省/非法 → undefined。 */
function parseRetryAfter(header: string | null): number | undefined {
  if (header === null) return undefined;
  const trimmed = header.trim();
  if (trimmed === '') return undefined;
  if (/^\d+$/.test(trimmed)) {
    return Number.parseInt(trimmed, 10);
  }
  const dateMs = Date.parse(trimmed);
  if (Number.isNaN(dateMs)) return undefined;
  const seconds = Math.floor((dateMs - Date.now()) / 1000);
  return Math.max(0, seconds);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isSingleEnvelope(value: unknown): value is { data: unknown } {
  return isRecord(value) && 'data' in value;
}

function isListEnvelopeShape(value: unknown): value is ListEnvelope<unknown> {
  return isRecord(value) && Array.isArray((value as { data?: unknown }).data);
}

function isGroupedEnvelopeShape(value: unknown): value is GroupedEnvelope<unknown> {
  return isRecord(value) && Array.isArray((value as { groups?: unknown }).groups);
}

/**
 * 默认 fetch 经箭头函数转发:浏览器中 `window.fetch` 以错误接收者调用会抛
 * "Illegal invocation",实例方法 `this.fetchImpl(…)` 恰好如此;裸调用规避之。
 */
const defaultFetchImpl: typeof fetch = (...args) => fetch(...args);

export class MeshApiClient {
  private readonly baseUrl: string;

  private readonly getToken: () => string | null;

  private readonly fetchImpl: typeof fetch;

  private readonly onUnauthorized: (() => void) | undefined;

  constructor(opts: ClientOptions) {
    this.baseUrl = opts.baseUrl;
    this.getToken = opts.getToken;
    this.fetchImpl = opts.fetchImpl ?? defaultFetchImpl;
    this.onUnauthorized = opts.onUnauthorized;
  }

  /** 单对象包络解析:返回 `data`;204/空体返回 undefined。 */
  async request<T>(method: HttpMethod, path: string, opts: RequestOptions = {}): Promise<T> {
    const response = await this.execute(method, path, opts);
    const text = await response.text();
    if (response.status === HTTP_NO_CONTENT || text.trim() === '') {
      return undefined as unknown as T;
    }
    const parsed = this.parseJson(response.status, text);
    if (!isSingleEnvelope(parsed)) {
      throw this.envelopeMismatch(response.status);
    }
    return parsed.data as T;
  }

  /** 列表包络:原样返回 `{data, next_cursor}`(next_cursor=null 为末页)。 */
  async list<T>(path: string, opts: RequestOptions = {}): Promise<ListEnvelope<T>> {
    const response = await this.execute('GET', path, opts);
    const parsed = await this.readEnvelope(response);
    if (!isListEnvelopeShape(parsed)) {
      throw this.envelopeMismatch(response.status);
    }
    // 形状已校验为列表包络;此处将 unknown 元素收窄到调用方声明的 T。
    return parsed as ListEnvelope<T>;
  }

  /** 分组包络:原样返回 `{groups, next_cursor}`(整体游标,§6.14)。 */
  async grouped<T>(path: string, opts: RequestOptions = {}): Promise<GroupedEnvelope<T>> {
    const response = await this.execute('GET', path, opts);
    const parsed = await this.readEnvelope(response);
    if (!isGroupedEnvelopeShape(parsed)) {
      throw this.envelopeMismatch(response.status);
    }
    // 形状已校验为分组包络;此处将 unknown 元素收窄到调用方声明的 T。
    return parsed as GroupedEnvelope<T>;
  }

  /** 读取 2xx 包络体;空体视为形状不符(列表/分组必须有包络)。 */
  private async readEnvelope(response: Response): Promise<unknown> {
    const text = await response.text();
    if (text.trim() === '') {
      throw this.envelopeMismatch(response.status);
    }
    return this.parseJson(response.status, text);
  }

  private parseJson(status: number, text: string): unknown {
    try {
      return JSON.parse(text) as unknown;
    } catch {
      throw this.envelopeMismatch(status);
    }
  }

  private envelopeMismatch(status: number): MeshApiError {
    return new MeshApiError({
      status,
      code: 'internal_error',
      message: `HTTP ${status}`,
    });
  }

  /** 执行请求:组装头/体、发起 fetch;非 2xx 与网络失败统一抛 MeshApiError。 */
  private async execute(method: HttpMethod, path: string, opts: RequestOptions): Promise<Response> {
    const url = buildUrl(this.baseUrl, path, opts.query);
    // 复制一份请求头,绝不就地修改调用方传入的 opts/headers(不可变)。
    const headers: Record<string, string> = { ...opts.headers };

    const token = this.getToken();
    if (token !== null) {
      headers[AUTH_HEADER] = bearerHeader(token);
    }

    const hasBody = opts.body !== undefined;
    if (hasBody) {
      headers[CONTENT_TYPE_HEADER] = CONTENT_TYPE_JSON;
    }

    if (opts.ifMatch !== undefined) {
      headers[IF_MATCH_HEADER] = opts.ifMatch;
    }

    if (IDEMPOTENT_METHODS.has(method)) {
      headers[IDEMPOTENCY_KEY_HEADER] = opts.idempotencyKey ?? crypto.randomUUID();
    }

    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        method,
        headers,
        body: hasBody ? JSON.stringify(opts.body) : undefined,
        signal: opts.signal,
      });
    } catch {
      throw new MeshApiError({ status: 0, code: 'network', message: 'network error' });
    }

    if (!response.ok) {
      // MES-106:非鉴权豁免端点的 401 = 会话失效 → 全局兜底(清 token + 跳登录)。
      if (response.status === HTTP_UNAUTHORIZED && !isAuthExemptPath(path)) {
        this.onUnauthorized?.();
      }
      throw await this.buildHttpError(response);
    }
    return response;
  }

  private async buildHttpError(response: Response): Promise<MeshApiError> {
    const retryAfter =
      response.status === HTTP_TOO_MANY_REQUESTS
        ? parseRetryAfter(response.headers.get(RETRY_AFTER_HEADER))
        : undefined;

    let body: unknown;
    try {
      const text = await response.text();
      body = text.trim() === '' ? undefined : (JSON.parse(text) as unknown);
    } catch {
      body = undefined;
    }

    if (isErrorEnvelope(body)) {
      return new MeshApiError({
        status: response.status,
        code: body.error.code,
        message: body.error.message,
        details: body.error.details,
        retryAfter,
      });
    }
    return new MeshApiError({
      status: response.status,
      code: 'internal_error',
      message: `HTTP ${response.status}`,
      retryAfter,
    });
  }
}
