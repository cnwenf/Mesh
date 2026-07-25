/**
 * 统一 API 错误类型 — 权威:docs/specs/README.md §6.14。
 * 后端错误信封 `{"error":{"code","message","details?"}}` 经 MeshApiClient 归一为本类型;
 * 面向用户的文案由前端按 `error.<code>` 渲染(§6.18/§3.4),后端 message 保持中性。
 */

export interface MeshApiErrorOptions {
  status: number;
  code: string;
  message: string;
  details?: Record<string, unknown>;
  retryAfter?: number;
}

export class MeshApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;
  /** 429 rate_limited 的 Retry-After(秒);其余状态为 undefined */
  readonly retryAfter?: number;

  constructor(opts: MeshApiErrorOptions) {
    super(opts.message);
    this.name = 'MeshApiError';
    this.status = opts.status;
    this.code = opts.code;
    this.details = opts.details;
    this.retryAfter = opts.retryAfter;
  }
}

/** 将错误映射为 i18n 消息键;本地化文案归消息目录所有(§6.18)。 */
export function errorToI18nKey(err: MeshApiError): string {
  return `error.${err.code}`;
}
