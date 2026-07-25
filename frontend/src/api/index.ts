/**
 * API 契约层桶导出(README §6.14 / §6.5)。
 * 仅再导出本模块公共符号;包络/实体类型见 `types/`。
 */
export { MeshApiError, errorToI18nKey } from './errors';
export type { MeshApiErrorOptions } from './errors';
export { AUTH_HEADER, bearerHeader, getToken, useAuthStore } from './tokenStore';
export { MeshApiClient } from './client';
export type { ClientOptions, HttpMethod, RequestOptions } from './client';
export { fetchAllPages, useCursorPagination } from './pagination';
export type { CursorPage } from './pagination';
export { optimisticUpdate, useOptimisticMutation } from './optimistic';
export type {
  OptimisticMutation,
  OptimisticPlan,
  OptimisticResult,
  UseOptimisticMutationOptions,
} from './optimistic';
export {
  MAX_FILTER_CONDITIONS,
  MAX_FILTER_DEPTH,
  classifyFilterError,
  measureFilters,
  validateFilters,
} from './filters';
export type { FilterCondition, FilterGroup, FilterMeasurement, FilterNode } from './filters';
