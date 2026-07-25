/**
 * API 包络类型 — 权威:docs/specs/README.md §6.14。
 * - 单对象:`{"data": {...}}`
 * - 列表:`{"data": [...], "next_cursor": <opaque|null>}`(next_cursor=null 表示末页)
 * - 分组(整体游标):`{"groups": [{key,label,count,wip?,data}], "next_cursor": ...}`
 * - 错误:`{"error": {"code", "message", "details?"}}`
 */

export interface SingleEnvelope<T> {
  data: T;
}

export interface ListEnvelope<T> {
  data: T[];
  next_cursor: string | null;
}

export interface Group<T> {
  key: string;
  label: string;
  /** 组内总数 */
  count: number;
  /** 看板 WIP 上限(可选) */
  wip?: number;
  /** 当前页切片 */
  data: T[];
}

export interface GroupedEnvelope<T> {
  groups: Group<T>[];
  next_cursor: string | null;
}

export interface ErrorBody {
  code: string;
  /** 后端保持英文/中性,不泄漏内部细节;面向用户的文案由前端按 error.<code> 渲染(§6.18) */
  message: string;
  details?: Record<string, unknown>;
}

export interface ErrorEnvelope {
  error: ErrorBody;
}

/** 类型守卫:是否为错误信封 */
export function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== 'object' || value === null) return false;
  const err = (value as { error?: unknown }).error;
  return typeof err === 'object' && err !== null && typeof (err as ErrorBody).code === 'string';
}
