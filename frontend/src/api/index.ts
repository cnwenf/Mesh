/**
 * API 契约层桶导出(README §6.14 / §6.5)。
 * 仅再导出本模块公共符号;包络/实体类型见 `types/`。
 */
export { MeshApiError, errorToI18nKey } from './errors';
export type { MeshApiErrorOptions } from './errors';
export { AUTH_HEADER, bearerHeader, getToken, useAuthStore } from './tokenStore';
export { MeshApiClient } from './client';
export type { ClientOptions, HttpMethod, RequestOptions } from './client';
export { getApiClient, resetApiClient } from './instance';
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
export {
  ERROR_INVALID_TIMEZONE,
  ERROR_UNSUPPORTED_LOCALE,
  fetchCurrentUserPreferences,
  updatePreferences,
} from './userPreferences';
export type { ServerUserPreferences, UpdatePreferencesPayload } from './userPreferences';
export { fetchWorkspaceDefaultLocale, fetchWorkspaces } from './workspace';
export type { WorkspaceSettings, WorkspaceSummary } from './workspace';
