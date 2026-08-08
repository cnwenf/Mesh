/**
 * 收藏状态 hook(README §6.19,L222):
 * 按 (workspace, targetType) 拉取收藏列表,提供成员集合与乐观切换,
 * 供 issue 详情/看板行/视图/项目 ⋯ 菜单星标共用。
 * toggle 先乐观更新集合,失败回滚 + danger toast;PUT/DELETE 服务端均幂等。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../api';
import { deleteFavorite, listFavorites, putFavorite } from '../../api/favorites';
import type { FavoriteTargetType } from '../../api/favorites';
import { useToast } from '../../design';
import { env } from '../../env';
import { useT } from '../../i18n';

export interface UseFavoritesResult {
  /** 当前成员在该目标类型下已收藏的目标 id 集合。 */
  readonly favoriteIds: ReadonlySet<string>;
  /** 列表是否完成首次解析(含失败降级)。 */
  readonly isLoaded: boolean;
  /** toggle 在途(防连点重入)。 */
  readonly isToggling: boolean;
  /** 切换目标收藏态(乐观更新,失败回滚)。 */
  readonly toggle: (targetId: string) => Promise<void>;
}

export function useFavorites(
  workspaceId: string | null,
  targetType: FavoriteTargetType,
): UseFavoritesResult {
  const t = useT();
  const { addToast } = useToast();
  const client = useMemo(() => new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }), []);
  const [favoriteIds, setFavoriteIds] = useState<ReadonlySet<string>>(() => new Set<string>());
  const [isLoaded, setIsLoaded] = useState(false);
  const [isToggling, setIsToggling] = useState(false);

  useEffect(() => {
    if (workspaceId === null) return;
    let cancelled = false;
    setIsLoaded(false);
    void listFavorites(client, workspaceId, targetType)
      .then((entries) => {
        if (cancelled) return;
        setFavoriteIds(new Set(entries.map((entry) => entry.target_id)));
        setIsLoaded(true);
      })
      .catch(() => {
        // 列表拉取失败降级为空集合(菜单随后按「未收藏」呈现,PUT 仍可正常收藏),
        // 不打断宿主页面——收藏是辅助能力,不做整页错误态。
        if (!cancelled) setIsLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, targetType]);

  const toggle = useCallback(
    async (targetId: string): Promise<void> => {
      if (workspaceId === null || isToggling) return;
      const wasFavorite = favoriteIds.has(targetId);
      const optimistic = new Set(favoriteIds);
      if (wasFavorite) {
        optimistic.delete(targetId);
      } else {
        optimistic.add(targetId);
      }
      setFavoriteIds(optimistic);
      setIsToggling(true);
      try {
        if (wasFavorite) {
          await deleteFavorite(client, targetType, targetId);
        } else {
          await putFavorite(client, targetType, targetId);
        }
        addToast(t(wasFavorite ? 'favorites.removedToast' : 'favorites.addedToast'), {
          tone: 'success',
          closeLabel: t('a11y.dismiss'),
        });
      } catch (err: unknown) {
        // 乐观回滚 + 可见错误反馈(乐观更新约定)。
        setFavoriteIds(favoriteIds);
        addToast(t(err instanceof MeshApiError ? errorToI18nKey(err) : 'error.unknown'), {
          tone: 'danger',
          closeLabel: t('a11y.dismiss'),
        });
      } finally {
        setIsToggling(false);
      }
    },
    [workspaceId, isToggling, favoriteIds, client, targetType, addToast, t],
  );

  return { favoriteIds, isLoaded, isToggling, toggle };
}
