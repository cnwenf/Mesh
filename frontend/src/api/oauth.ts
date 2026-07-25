/**
 * OAuth 登录·绑定 API — auth.md §1.2 A5/A6, §3.1。
 *
 * `start`/`bind` 为 302 跳转(非 XHR):前端直接导航到 authorization URL,
 * 提供方回调 `/callback` 完成 code+PKCE 交换。`identities`/解绑为常规鉴权请求。
 * 后端 vendor 中立(仅 mock 提供商于 dev),前端不绑定任何具体厂商。
 *
 * SPA 往返(§4.1/§4.5 step 5):登录页将浏览器导航到 `start`(redirect_uri 指向
 * 前端回调路由 `/auth/oauth/callback/:provider`,须与后端 M1 redirect_uri
 * 精确白名单一致)→ 提供方回跳前端回调页 → 回调页经 `oauthCallbackLogin`
 * 向后端 callback 端点交换会话凭证。
 */
import type { MeshApiClient } from './client';
import type { SessionTokens } from './auth';

/** 400 错误码:state 无效/过期或 provider 不匹配(auth.md §3.1) */
export const ERROR_INVALID_OAUTH_STATE = 'invalid_oauth_state';
/** 422 错误码:redirect_uri 不在该提供商白名单(M1 开放重定向防护) */
export const ERROR_REDIRECT_NOT_ALLOWED = 'redirect_uri_not_allowed';

/** 登录往返间携带回跳路径的 sessionStorage 键(避免污染精确匹配的 redirect_uri) */
export const OAUTH_NEXT_STORAGE_KEY = 'mesh.oauth.next';

/** 提供商 ID 合法性守卫用模式:仅允许 URL 安全 slug(防路径注入) */
const PROVIDER_SLUG = /^[a-z0-9][a-z0-9_-]*$/i;

/** 已绑定的第三方身份(§3.1 GET /auth/oauth/identities) */
export interface OAuthIdentity {
  provider: string;
  provider_email: string | null;
  created_at: string;
}

/** 列出当前用户已绑定的第三方身份。 */
export async function listIdentities(client: MeshApiClient): Promise<OAuthIdentity[]> {
  const envelope = await client.list<OAuthIdentity>('/api/v1/auth/oauth/identities');
  return envelope.data;
}

/** 解绑第三方身份(保留至少一种登录方式;删最后一种 → 422 last_login_method)。 */
export async function unbindIdentity(client: MeshApiClient, provider: string): Promise<void> {
  await client.request('DELETE', `/api/v1/auth/oauth/${provider}`);
}

/** 构造登录跳转 URL(浏览器导航触发 302 → 提供方 → 回调)。 */
export function oauthLoginUrl(baseUrl: string, provider: string, redirectUri: string): string {
  const base = baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
  const params = new URLSearchParams({ redirect_uri: redirectUri });
  return `${base}/api/v1/auth/oauth/${provider}/start?${params.toString()}`;
}

/** 构造绑定跳转 URL(需已登录;浏览器导航触发)。 */
export function oauthBindUrl(baseUrl: string, provider: string, redirectUri: string): string {
  const base = baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
  const params = new URLSearchParams({ redirect_uri: redirectUri });
  return `${base}/api/v1/auth/oauth/${provider}/bind?${params.toString()}`;
}

/**
 * 前端回调路由的 redirect_uri(与后端 M1 精确白名单协同:每提供商一条,
 * 不带易变查询串)。`origin` 取当前站点,天然区分 dev/prod 部署。
 */
export function oauthRedirectUri(provider: string): string {
  return `${window.location.origin}/auth/oauth/callback/${provider}`;
}

/**
 * 回调交换(登录模式):用提供方回跳的 code+state 换会话凭证。
 * 非法 state → 400 `invalid_oauth_state`;provider 仅接受 URL 安全 slug。
 */
export async function oauthCallbackLogin(
  client: MeshApiClient,
  provider: string,
  code: string,
  state: string,
): Promise<SessionTokens> {
  if (!PROVIDER_SLUG.test(provider)) {
    throw new Error(`Invalid OAuth provider slug: ${provider}`);
  }
  const params = new URLSearchParams({ code, state });
  return client.request<SessionTokens>(
    'GET',
    `/api/v1/auth/oauth/${provider}/callback?${params.toString()}`,
  );
}
