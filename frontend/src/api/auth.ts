/**
 * 认证 API — auth.md §3.1(注册/登录/当前用户)。
 *
 * 消费后端 v0.2.0:登录返回 access JWT(900s)+ refresh token;
 * 错误具名码:422 invalid_credentials / 400 weak_password(details.reason)/ 409 conflict(field=email)。
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

/** 422 错误码:凭证错误(auth.md §3.1) */
export const ERROR_INVALID_CREDENTIALS = 'invalid_credentials';
/** 400 错误码:弱口令(details.reason ∈ too_short/needs_letter_and_digit/too_common) */
export const ERROR_WEAK_PASSWORD = 'weak_password';

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
