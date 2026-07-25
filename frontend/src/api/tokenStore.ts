/**
 * Bearer token 接入点 — 权威:docs/specs/README.md §6.14 鉴权。
 * token 的真源是 `state/authStore`(DRY:此处仅薄再导出 + 请求头工具)。
 */
export { getToken, useAuthStore } from '../state/authStore';

/** 标准鉴权请求头名(§6.14 `Authorization: Bearer <token>`) */
export const AUTH_HEADER = 'Authorization';

/** 由 token 构造 `Bearer <token>` 请求头值;调用方保证 token 非空。 */
export function bearerHeader(token: string): string {
  return `Bearer ${token}`;
}
