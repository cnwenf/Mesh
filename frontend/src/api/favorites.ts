/**
 * 收藏 API(README §6.19 统一 favorites 模型)。
 * PUT 幂等收藏 / DELETE 取消 / GET 列表(游标分页)。
 * 聊天会话置顶经 target_type='chat_session' 表达(chat/api.ts 同端点薄封装)。
 */
import type { MeshApiClient } from './client';

export type FavoriteTargetType = 'issue' | 'project' | 'view' | 'chat_session';

export interface FavoriteEntry {
  readonly target_type: string;
  readonly target_id: string;
}

const favoritePath = (targetType: FavoriteTargetType, targetId: string): string =>
  `/api/v1/favorites/${targetType}/${targetId}`;

export async function putFavorite(
  client: MeshApiClient,
  targetType: FavoriteTargetType,
  targetId: string,
): Promise<void> {
  await client.request<void>('PUT', favoritePath(targetType, targetId));
}

export async function deleteFavorite(
  client: MeshApiClient,
  targetType: FavoriteTargetType,
  targetId: string,
): Promise<void> {
  await client.request<void>('DELETE', favoritePath(targetType, targetId));
}

export async function listFavorites(
  client: MeshApiClient,
  workspaceId: string,
  targetType?: FavoriteTargetType,
): Promise<readonly FavoriteEntry[]> {
  const envelope = await client.list<FavoriteEntry>('/api/v1/favorites', {
    query: { workspace_id: workspaceId, target_type: targetType },
  });
  return envelope.data;
}
