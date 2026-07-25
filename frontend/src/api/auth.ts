/**
 * 认证 API — auth.md §3.1(注册/登录/续期/登出/重置/验证/MFA/会话)。
 *
 * 消费后端 auth 后端(增量 1 + 切片 2):登录返回 access JWT(900s)+ refresh;
 * 错误具名码:422 invalid_credentials / 400 weak_password(details.reason)/
 * 409 conflict(field=email)/ 423 account_locked / 429 rate_limited。
 */
import type { MeshApiClient } from './client';

/** 登录成功响应(会话凭证) */
export interface SessionTokens {
  access_token: string;
  token_type: string;
  expires_in: number;
  refresh_token: string;
}

/** 启用 MFA 时的登录响应(需二次验证) */
export interface MfaChallenge {
  mfa_required: true;
  mfa_ticket: string;
}

/** 登录结果:直接发牌或 MFA 质询 */
export type LoginResult = SessionTokens | MfaChallenge;

export interface LoginInput {
  email: string;
  password: string;
  remember?: boolean;
}

export interface RegisterInput {
  email: string;
  password: string;
  display_name: string;
}

/** 当前用户(GET /api/v1/me 响应;偏好键见 i18n.md §2.2) */
export interface CurrentUser {
  id: string;
  email: string;
  email_verified: boolean;
  display_name: string;
  avatar_url: string | null;
  status: string;
  timezone: string | null;
  settings: { locale?: string | null; theme?: string };
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
}

/** 活跃会话(§3.1 GET /sessions) */
export interface SessionInfo {
  id: string;
  type: string;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_active_at: string | null;
  expires_at: string;
  current: boolean;
}

/** MFA 启用向导(§3.1 POST /auth/mfa/setup):密钥 + otpauth URI + 备用码(仅一次) */
export interface MfaSetupInfo {
  secret: string;
  otpauth_uri: string;
  backup_codes: string[];
}

/** 422 错误码:凭证错误(auth.md §3.1) */
export const ERROR_INVALID_CREDENTIALS = 'invalid_credentials';
/** 400 错误码:弱口令(details.reason ∈ too_short/needs_letter_and_digit/too_common) */
export const ERROR_WEAK_PASSWORD = 'weak_password';
/** 423 错误码:登录锁定(失败过多) */
export const ERROR_ACCOUNT_LOCKED = 'account_locked';

/** 类型判别:登录结果是否为会话凭证(否则为 MFA 质询) */
export function isSessionTokens(result: LoginResult): result is SessionTokens {
  return 'access_token' in result;
}

/** 邮箱/密码登录 → 会话凭证(或 MFA 质询)。 */
export async function login(client: MeshApiClient, input: LoginInput): Promise<LoginResult> {
  return client.request<LoginResult>('POST', '/api/v1/auth/login', { body: input });
}

/** 注册账号(409 conflict = 邮箱已占用;400 weak_password = 口令强度不足)。 */
export async function register(
  client: MeshApiClient,
  input: RegisterInput,
): Promise<CurrentUser> {
  return client.request<CurrentUser>('POST', '/api/v1/auth/register', { body: input });
}

/** 读取当前登录用户(需 Bearer token)。 */
export async function fetchMe(client: MeshApiClient): Promise<CurrentUser> {
  return client.request<CurrentUser>('GET', '/api/v1/me');
}

/** refresh 续期:用 refresh token 换新 access(+ 轮换 refresh)。 */
export async function refresh(
  client: MeshApiClient,
  refreshToken: string,
): Promise<SessionTokens> {
  return client.request<SessionTokens>('POST', '/api/v1/auth/refresh', {
    body: { refresh_token: refreshToken },
  });
}

/** 登出当前会话(撤销指定 refresh)。 */
export async function logout(client: MeshApiClient, refreshToken: string): Promise<void> {
  await client.request('POST', '/api/v1/auth/logout', { body: { refresh_token: refreshToken } });
}

/** 登出全部会话(撤销该用户所有 refresh)。 */
export async function logoutAll(client: MeshApiClient): Promise<{ revoked: number }> {
  return client.request<{ revoked: number }>('POST', '/api/v1/auth/logout-all', { body: {} });
}

/** 发起密码重置(恒成功,防枚举;dev 令牌入 Redis dev-mailbox)。 */
export async function forgotPassword(client: MeshApiClient, email: string): Promise<void> {
  await client.request('POST', '/api/v1/auth/forgot-password', { body: { email } });
}

/** 凭重置令牌设新密码(并使旧会话失效)。 */
export async function resetPassword(
  client: MeshApiClient,
  token: string,
  newPassword: string,
): Promise<void> {
  await client.request('POST', '/api/v1/auth/reset-password', {
    body: { token, new_password: newPassword },
  });
}

/** 验证邮箱(凭验证令牌)。 */
export async function verifyEmail(client: MeshApiClient, token: string): Promise<void> {
  await client.request('POST', '/api/v1/auth/verify-email', { body: { token } });
}

// --- MFA(§3.1)--------------------------------------------------------------

/** 生成 TOTP 密钥 + otpauth URI + 备用码(尚未启用)。 */
export async function mfaSetup(client: MeshApiClient): Promise<MfaSetupInfo> {
  return client.request<MfaSetupInfo>('POST', '/api/v1/auth/mfa/setup', { body: {} });
}

/** 验证码确认后启用 MFA。 */
export async function mfaEnable(client: MeshApiClient, code: string): Promise<void> {
  await client.request('POST', '/api/v1/auth/mfa/enable', { body: { code } });
}

/** 验证码确认后停用 MFA。 */
export async function mfaDisable(client: MeshApiClient, code: string): Promise<void> {
  await client.request('POST', '/api/v1/auth/mfa/disable', { body: { code } });
}

/** 登录二次验证:凭 mfa_ticket + TOTP/备用码换会话凭证。 */
export async function mfaVerify(
  client: MeshApiClient,
  mfaTicket: string,
  code: string,
): Promise<SessionTokens> {
  return client.request<SessionTokens>('POST', '/api/v1/auth/mfa/verify', {
    body: { mfa_ticket: mfaTicket, code },
  });
}

// --- 会话管理(§3.1)---------------------------------------------------------

/** 列出活跃会话。 */
export async function listSessions(client: MeshApiClient): Promise<SessionInfo[]> {
  const envelope = await client.list<SessionInfo>('/api/v1/sessions');
  return envelope.data;
}

/** 撤销指定会话。 */
export async function revokeSession(client: MeshApiClient, sessionId: string): Promise<void> {
  await client.request('DELETE', `/api/v1/sessions/${sessionId}`);
}
