/**
 * API token(PAT / agent 凭证)API — auth.md §3.2。
 *
 * 管理端点以当前用户(access JWT)经 `require_workspace("token:manage")` 鉴权;
 * 创建响应**仅此一次**返回明文 `token`,之后列表只给 `prefix` + 掩码。
 * `whoami` 以 PAT 自身作 Bearer(独立客户端,不复用会话 access token)。
 */
import { MeshApiClient } from './client';

/** 列表/详情中的 token(无明文,仅 prefix + 元数据) */
export interface ApiTokenInfo {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  role_override: string | null;
  owner_member_id: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

/** 创建响应:在 ApiTokenInfo 之上附带**一次性**明文 token */
export interface CreatedApiToken extends ApiTokenInfo {
  token: string;
}

export interface CreateTokenInput {
  name: string;
  scopes?: string[];
  role_override?: string | null;
  expires_at?: string | null;
  owner_member_id?: string | null;
}

/** PAT whoami 解析出的有效 principal */
export interface TokenPrincipal {
  token_id: string;
  workspace_id: string;
  owner_member_id: string;
  member_type: string;
  role: string;
  scopes: string[];
  name: string;
}

/** 列出当前用户在该工作区可见的 token(member 仅自己 / admin 全部)。 */
export async function listTokens(
  client: MeshApiClient,
  workspaceId: string,
): Promise<ApiTokenInfo[]> {
  const envelope = await client.list<ApiTokenInfo>(`/api/v1/workspaces/${workspaceId}/api-tokens`);
  return envelope.data;
}

/** 创建 token —— 响应**仅一次**含明文 `token`(关闭后无法再查看)。 */
export async function createToken(
  client: MeshApiClient,
  workspaceId: string,
  input: CreateTokenInput,
): Promise<CreatedApiToken> {
  return client.request<CreatedApiToken>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/api-tokens`,
    { body: input },
  );
}

/** 撤销 token(即时失效;持有者或 admin)。 */
export async function revokeToken(
  client: MeshApiClient,
  workspaceId: string,
  tokenId: string,
): Promise<void> {
  await client.request('DELETE', `/api/v1/workspaces/${workspaceId}/api-tokens/${tokenId}`);
}

/** 以 PAT 自身鉴权,解析其有效 principal(workspace/role/scopes)。 */
export async function tokenWhoami(
  baseUrl: string,
  pat: string,
  fetchImpl?: typeof fetch,
): Promise<TokenPrincipal> {
  const patClient = new MeshApiClient({ baseUrl, getToken: () => pat, fetchImpl });
  return patClient.request<TokenPrincipal>('GET', '/api/v1/api-tokens/whoami');
}
