/**
 * 面板数据编排 hook —— 命令面板对话框与顶栏搜索弹层共用(§4.9 同一结果视图):
 *
 * - 本地命令同步过滤(零延迟,首帧即渲染;§11.4 远程 skeleton 不阻塞本地命令);
 * - 实体结果经 useEntitySearch(防抖/可取消/旧响应丢弃);
 * - 空 query 唯一数据流(§4.2.1):favorites(开启时拉取,可注入 provider 以便测试)+
 *   recents + 常用命令;recents 作用域(三元组隔离)在此设定;
 * - 错误/重试状态透传;offline(network 错误)由呈现层据此降级本地命令。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { getApiClient } from '../api/instance';
import type { MeshApiError } from '../api/errors';
import { listPaletteFavorites } from '../api/search';
import type { FavoriteTargetType, PaletteFavorite, SearchItem } from '../api/search';
import {
  collectValidRecentKeys,
  resolveFavoriteTargets,
} from '../features/search/favoritesResolve';
import { buildEmptySections, buildQuerySections, flattenSections } from './paletteModel';
import type { PaletteSection } from './paletteModel';
import { setRecentsScope } from './recents';
import { commandUseCounts, listRecents, removeRecent } from './recents';
import { useEntitySearch } from './useEntitySearch';
import { useShortcutRegistry } from './registry';

/** favorites 数据源注入点(测试桩;缺省走 GET /api/v1/favorites,§6.19) */
export type FavoritesProvider = (workspaceId: string) => Promise<ReadonlyArray<PaletteFavorite>>;

export const defaultFavoritesProvider: FavoritesProvider = (workspaceId) =>
  listPaletteFavorites(getApiClient(), workspaceId);

const FAVORITE_TARGET_TYPES: ReadonlySet<FavoriteTargetType> = new Set([
  'issue',
  'project',
  'view',
  'chat_session',
]);

export interface UsePaletteDataArgs {
  readonly workspaceId: string | null;
  readonly workspaceSlug?: string | null;
  readonly userId: string | null;
  readonly query: string;
  readonly enabled: boolean;
  readonly favoritesProvider?: FavoritesProvider;
}

export interface UsePaletteDataResult {
  readonly sections: ReadonlyArray<PaletteSection>;
  readonly flatCount: number;
  readonly entityCount: number;
  readonly isSearching: boolean;
  readonly error: MeshApiError | null;
  readonly retry: () => void;
  /** 每次搜索落地后自增(供 live region 播报结果数,§9.6 第 7 点) */
  readonly settledToken: number;
  /** 本地存储变化后重算空态(recents/命令计数写入后调用) */
  readonly noteLocalChange: () => void;
}

export function usePaletteData(args: UsePaletteDataArgs): UsePaletteDataResult {
  const { workspaceId, userId, query, enabled } = args;
  const workspaceSlug = args.workspaceSlug ?? null;
  const favoritesProvider = args.favoritesProvider ?? defaultFavoritesProvider;
  const commands = useShortcutRegistry((state) => state.commands);
  const trimmed = query.trim();

  const search = useEntitySearch({ workspaceId, query, enabled });
  const [favorites, setFavorites] = useState<ReadonlyArray<PaletteFavorite>>([]);
  const [localVersion, setLocalVersion] = useState(0);
  const [settledToken, setSettledToken] = useState(0);

  // recents 三元组隔离作用域(§2.1)
  useEffect(() => {
    setRecentsScope({ userId: userId ?? 'anonymous', workspaceId: workspaceId ?? 'none' });
  }, [userId, workspaceId]);

  // favorites:每次开启拉取一次(空 query 唯一服务端数据源,§4.2.1)
  useEffect(() => {
    if (!enabled || workspaceId === null) {
      setFavorites([]);
      return;
    }
    setFavorites([]);
    let cancelled = false;
    favoritesProvider(workspaceId)
      .then(async (entries) => {
        const directlyRenderable = entries.filter(
          (entry) => entry.title !== undefined && entry.url !== undefined,
        );
        const resolvable = entries.filter(
          (entry): entry is PaletteFavorite & { readonly target_type: FavoriteTargetType } =>
            (entry.title === undefined || entry.url === undefined) &&
            FAVORITE_TARGET_TYPES.has(entry.target_type as FavoriteTargetType),
        );
        const resolved = await resolveFavoriteTargets(getApiClient(), resolvable, {
          workspaceId,
          workspaceSlug,
        });
        const hydrated = resolvable.flatMap((entry) => {
          const target = resolved.get(`${entry.target_type}:${entry.target_id}`);
          return target === undefined
            ? []
            : [
                {
                  ...entry,
                  title: entry.title ?? target.title,
                  url: entry.url ?? target.url,
                },
              ];
        });
        if (!cancelled) {
          // 未知类型/失效/瞬态不可解析的裸 id 均不渲染,避免 UUID 死行。
          setFavorites([...directlyRenderable, ...hydrated]);
        }
      })
      .catch(() => {
        // favorites 不可得时空态降级为 recents + 命令(非致命,§4.2 异常态)
        if (!cancelled) {
          setFavorites([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, workspaceId, workspaceSlug, favoritesProvider]);

  // 打开即核验对象 recents:403/404 代表已删或失权,从 UI 与持久化同步剪枝;
  // 瞬态错误由 collectValidRecentKeys 保留,避免网络抖动误删本地历史。
  useEffect(() => {
    if (!enabled || workspaceId === null) {
      return;
    }
    const scope = {
      userId: userId ?? 'anonymous',
      workspaceId,
    };
    const objectRecents = listRecents(scope).filter(
      (entry): entry is typeof entry & { readonly type: NonNullable<typeof entry.type> } =>
        entry.kind === 'object' && entry.type !== undefined,
    );
    if (objectRecents.length === 0) {
      return;
    }
    const snapshotKeys = new Set(objectRecents.map((entry) => `${entry.type}:${entry.id}`));
    let cancelled = false;
    void collectValidRecentKeys(getApiClient(), objectRecents, { workspaceId, workspaceSlug }).then(
      (validKeys) => {
        if (cancelled) return;
        removeRecent((entry) => {
          if (entry.kind !== 'object' || entry.type === undefined) return false;
          const key = `${entry.type}:${entry.id}`;
          // Only remove targets from the validated snapshot. A different
          // tab may append a recent while the detail requests are pending.
          return snapshotKeys.has(key) && !validKeys.has(key);
        }, scope);
        setLocalVersion((version) => version + 1);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [enabled, workspaceId, workspaceSlug, userId]);

  // 本地存储版本:每次开启重读 recents;激活写入后亦 bump
  useEffect(() => {
    if (enabled) {
      setLocalVersion((version) => version + 1);
    }
  }, [enabled]);

  const noteLocalChange = useCallback((): void => {
    setLocalVersion((version) => version + 1);
  }, []);

  const sections = useMemo<ReadonlyArray<PaletteSection>>(() => {
    if (trimmed === '') {
      return buildEmptySections({
        favorites,
        recents: listRecents({
          userId: userId ?? 'anonymous',
          workspaceId: workspaceId ?? 'none',
        }),
        commands,
        usageCounts: commandUseCounts(),
      });
    }
    return buildQuerySections(search.items, commands, trimmed);
    // localVersion 参与依赖:recents/命令计数经其重读(读取发生在 memo 内)
  }, [trimmed, favorites, search.items, commands, localVersion, userId, workspaceId]);

  // 结果落地令牌:检索结束后变化,驱动 live region 播报
  useEffect(() => {
    if (!search.isSearching) {
      setSettledToken((token) => token + 1);
    }
  }, [search.isSearching, sections]);

  const flatCount = useMemo(() => flattenSections(sections).length, [sections]);
  const entityCount = search.items.length;

  return {
    sections,
    flatCount,
    entityCount,
    isSearching: search.isSearching,
    error: search.error,
    retry: search.retry,
    settledToken,
    noteLocalChange,
  };
}

/** 供呈现层判定 offline 降级(navigator.onLine false 或 network 错误) */
export function isOfflineCondition(isOnline: boolean, error: MeshApiError | null): boolean {
  return !isOnline || error?.code === 'network';
}

/** 仅供类型转出的内部辅助(保持 SearchItem 在编排层可见) */
export type { SearchItem };
