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
export {
  createWorkspace,
  deleteWorkspace,
  fetchAllWorkspaceSummaries,
  fetchWorkspaceDefaultLocale,
  fetchWorkspaces,
  getWorkspace,
  getWorkspaceBySlug,
  listWorkspaces,
  restoreWorkspace,
  updateWorkspace,
} from './workspace';
export type {
  CreateWorkspaceInput,
  WorkspaceDetail,
  WorkspaceListQuery,
  WorkspacePatch,
  WorkspaceRole,
  WorkspaceSettings,
  WorkspaceSummary,
} from './workspace';
export {
  ERROR_INVITATION_INVALID,
  ERROR_INVITATION_LIMITS_EXCEEDED,
  acceptInvitation,
  createInvitations,
  listInvitations,
  previewInvitation,
  revokeInvitation,
} from './invitations';
export type {
  AcceptInvitationResult,
  CreateInvitationInput,
  Invitation,
  InvitationListQuery,
  InvitationRejectReason,
  InvitationRole,
  InvitationPreview,
  InvitationStatus,
} from './invitations';
export {
  ERROR_AGENT_OWNER_NOT_ALLOWED,
  ERROR_LAST_OWNER,
  listMembers,
  updateMemberRole,
} from './members';
export type {
  MemberListQuery,
  MemberStatus,
  MemberSummary,
  MemberType,
} from './members';
export {
  ERROR_ACCOUNT_LOCKED,
  ERROR_INVALID_CREDENTIALS,
  ERROR_WEAK_PASSWORD,
  fetchMe,
  forgotPassword,
  isSessionTokens,
  listSessions,
  login,
  logout,
  logoutAll,
  mfaDisable,
  mfaEnable,
  mfaSetup,
  mfaVerify,
  refresh,
  register,
  resetPassword,
  revokeSession,
  verifyEmail,
} from './auth';
export type {
  CurrentUser,
  LoginInput,
  LoginResult,
  MfaChallenge,
  MfaSetupInfo,
  RegisterInput,
  SessionInfo,
  SessionTokens,
} from './auth';
export { createToken, listTokens, revokeToken, tokenWhoami } from './tokens';
export type { ApiTokenInfo, CreateTokenInput, CreatedApiToken, TokenPrincipal } from './tokens';
export {
  ERROR_INVALID_OAUTH_STATE,
  ERROR_REDIRECT_NOT_ALLOWED,
  OAUTH_NEXT_STORAGE_KEY,
  listIdentities,
  oauthBindUrl,
  oauthCallbackLogin,
  oauthLoginUrl,
  oauthRedirectUri,
  unbindIdentity,
} from './oauth';
export type { OAuthIdentity } from './oauth';
export { listAuditLogs } from './audit';
export type { AuditLogEntry, AuditLogQuery } from './audit';
