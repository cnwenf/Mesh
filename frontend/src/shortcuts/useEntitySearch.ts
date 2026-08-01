/**
 * 实体搜索 hook(design-quality §9.6 第 4 点 / search-command-palette.md §4.7):
 *
 * - 本地 DEBOUNCE_MS(120ms)防抖;**完整 identifier 查询跳过防抖**(§2.2 快路径);
 * - AbortController:新查询/卸载即中止在途请求(请求可取消);
 * - 单调请求令牌:旧请求的响应一律丢弃,不覆盖新查询(旧响应不得覆盖新查询);
 * - 错误保留为 MeshApiError 并提供 retry();中止不计为错误;
 * - workspaceId 为空 / 未启用 / 空 query → 不请求、空结果。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { MeshApiError } from '../api/errors';
import { getApiClient } from '../api/instance';
import { isIdentifierQuery, searchWorkspace } from '../api/search';
import type { SearchItem } from '../api/search';

/** 远程检索防抖(毫秒,§9.6) */
export const DEBOUNCE_MS = 120;

export interface UseEntitySearchArgs {
  /** 工作区 id(UUID);null → 不请求 */
  readonly workspaceId: string | null;
  readonly query: string;
  /** 面板/弹层未打开时不请求 */
  readonly enabled: boolean;
}

export interface UseEntitySearchResult {
  readonly items: ReadonlyArray<SearchItem>;
  /** 防抖等待或在途均为 true */
  readonly isSearching: boolean;
  readonly error: MeshApiError | null;
  /** 以当前 query 重新请求(错误行「重试」) */
  readonly retry: () => void;
}

/** 非 MeshApiError 抛出归一为 network 错误(前端兜底) */
function toMeshApiError(error: unknown): MeshApiError {
  if (error instanceof MeshApiError) {
    return error;
  }
  return new MeshApiError({ status: 0, code: 'network', message: 'network error' });
}

export function useEntitySearch(args: UseEntitySearchArgs): UseEntitySearchResult {
  const { workspaceId, query, enabled } = args;
  const [items, setItems] = useState<ReadonlyArray<SearchItem>>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<MeshApiError | null>(null);
  const [retryToken, setRetryToken] = useState(0);
  /** 单调请求令牌:每次发起自增;响应落地时比对,旧令牌的响应丢弃 */
  const requestTokenRef = useRef(0);

  const retry = useCallback((): void => {
    setRetryToken((token) => token + 1);
  }, []);

  const trimmed = query.trim();

  useEffect(() => {
    if (!enabled || workspaceId === null || trimmed === '') {
      requestTokenRef.current += 1; // 使任何在途响应失效
      setItems([]);
      setIsSearching(false);
      setError(null);
      return;
    }

    const controller = new AbortController();
    const token = (requestTokenRef.current += 1);
    let timerId: number | undefined;

    const run = async (): Promise<void> => {
      setIsSearching(true);
      try {
        const page = await searchWorkspace(getApiClient(), workspaceId, {
          q: trimmed,
          signal: controller.signal,
        });
        if (requestTokenRef.current !== token) {
          return; // 陈旧响应:丢弃(§9.6 旧响应不覆盖新查询)
        }
        setItems(page.data);
        setError(null);
        setIsSearching(false);
      } catch (caught) {
        if (controller.signal.aborted || requestTokenRef.current !== token) {
          return; // 中止/陈旧:不作为错误呈现
        }
        setItems([]);
        setError(toMeshApiError(caught));
        setIsSearching(false);
      }
    };

    if (isIdentifierQuery(trimmed)) {
      void run(); // identifier 快路径:跳过防抖(§2.2)
    } else {
      setIsSearching(true); // 防抖等待期即呈现检索态(顶部进度条)
      timerId = window.setTimeout(() => {
        void run();
      }, DEBOUNCE_MS);
    }

    return () => {
      controller.abort();
      if (timerId !== undefined) {
        window.clearTimeout(timerId);
      }
    };
  }, [enabled, workspaceId, trimmed, retryToken]);

  return { items, isSearching, error, retry };
}
