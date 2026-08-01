/**
 * 认证 API — auth.md §3.1(注册/登录/续期/登出/重置/验证/MFA/会话)。
 *
 * Web 刷新契约(R4-H1):refresh 令牌**仅**经 HttpOnly cookie(`mesh_session`,
 * SameSite=Strict)下发——登录/注册响应体绝无 refresh 明文,JS 无从读取;
 * 续期经 cookie 自动呈递(同源请求浏览器自动携带),轮换经 Set-Cookie 下发。
 * 错误具名码:422 invalid_credentials / 400 weak_password(details.reason)/
 * 409 conflict(field=email)/ 423 account_locked / 429 rate_limited。
 */
import type { MeshApiClient } from './client';

/** 登录成功响应(仅 access;refresh 走 HttpOnly cookie,R4-H1) */
export interface SessionTokens {
  access_token: string;
  token_type: string;
  expires_in: number;
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

/** Agent credential resolved by the unified `GET /api/v1/me` principal endpoint. */
export interface CurrentAgentPrincipal {
  kind: 'agent';
  id: string;
  member_type: 'agent';
  workspace_id: string;
  role: string;
  name: string | null;
  scopes: string[];
}

/** `/me` is credential-polymorphic: web/session principals are users, agent tokens are roster identities. */
export type CurrentPrincipal = CurrentUser | CurrentAgentPrincipal;

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
export async function register(client: MeshApiClient, input: RegisterInput): Promise<CurrentUser> {
  return client.request<CurrentUser>('POST', '/api/v1/auth/register', { body: input });
}

/** 读取当前登录用户(需 Bearer token)。 */
export async function fetchMe(client: MeshApiClient): Promise<CurrentUser> {
  return client.request<CurrentUser>('GET', '/api/v1/me');
}

/** Read the effective principal without assuming the credential belongs to a human user. */
export async function fetchPrincipal(client: MeshApiClient): Promise<CurrentPrincipal> {
  return client.request<CurrentPrincipal>('GET', '/api/v1/me');
}

export function isAgentPrincipal(principal: CurrentPrincipal): principal is CurrentAgentPrincipal {
  return 'kind' in principal && principal.kind === 'agent';
}

/** refresh 续期(R4-H1 cookie 传输):refresh 经 HttpOnly cookie 自动呈递,
 * 请求体为空;胜者轮换经 Set-Cookie 下发,响应体仅新 access。 */
export async function refresh(client: MeshApiClient): Promise<SessionTokens> {
  return client.request<SessionTokens>('POST', '/api/v1/auth/refresh', {});
}

/** 登出当前会话(cookie 呈递识别会话;清理 HttpOnly cookie 由服务端下发)。 */
export async function logout(client: MeshApiClient): Promise<void> {
  await client.request('POST', '/api/v1/auth/logout', {});
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

/** 已登录态修改密码输入(§3.1 POST /auth/change-password,MES-39 / R7-M1)。
 * 当前会话经 access JWT 的 sid 识别并保留(不经 body 呈递 refresh)。 */
export interface ChangePasswordInput {
  oldPassword: string;
  newPassword: string;
}

/** 已登录态修改密码(§4.2):旧密码校验(422 invalid_credentials)+ 新密码强度
 * (400 weak_password,三 reason)。成功使其它会话失效,当前会话(sid 识别)保留。 */
export async function changePassword(
  client: MeshApiClient,
  input: ChangePasswordInput,
): Promise<void> {
  await client.request('POST', '/api/v1/auth/change-password', {
    body: {
      old_password: input.oldPassword,
      new_password: input.newPassword,
    },
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

// --- 设备码授权确认页(auth.md §3.1.1,cli.md §3.2)----------------------------

/** 确认页数据:client 名称 + 请求 scope 的人类可读枚举 + 批准者工作区列表(0/1/多分流) */
export interface DeviceConfirmation {
  client_name: string;
  requested_scopes: { scope: string; description: string }[];
  workspaces: { id: string; slug: string; name: string; my_role: string }[];
}

/** 读取设备码确认页数据(登录态;user_code 未命中 → 404,不区分原因防探测)。 */
export async function fetchDeviceConfirmation(
  client: MeshApiClient,
  userCode: string,
): Promise<DeviceConfirmation> {
  return client.request<DeviceConfirmation>('GET', '/api/v1/auth/device', {
    query: { user_code: userCode },
  });
}

/** 批准:绑定所录入的 user_code 与显式选定的工作区(scope 服务端取交)。 */
export async function approveDevice(
  client: MeshApiClient,
  userCode: string,
  workspaceId: string,
): Promise<{ status: string; granted_scopes?: string[] }> {
  return client.request('POST', '/api/v1/auth/device/approve', {
    body: { user_code: userCode, workspace_id: workspaceId },
  });
}

/** 拒绝所录入的 user_code(终态幂等回显当前状态)。 */
export async function denyDevice(
  client: MeshApiClient,
  userCode: string,
): Promise<{ status: string }> {
  return client.request('POST', '/api/v1/auth/device/deny', {
    body: { user_code: userCode },
  });
}
