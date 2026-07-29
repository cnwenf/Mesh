/**
 * 实体搜索 hook(search-command-palette.md §4.7:防抖 150ms + 过期请求取消)。
 *
 * - 本地命令过滤在面板层同步进行(零延迟);本 hook 只负责服务端对象检索;
 * - 防抖:query 变化后等待 debounceMs 再发请求;完整 identifier 形态
 *   (`KEY-N`,大小写不敏感)跳过防抖即刻请求(§2.2 等值快路径,命中顶置);
 * - 竞态治理:每次发请求前 abort 上一在途请求(AbortController),且以单调代次
 *   守卫丢弃迟到响应;被 abort 的失败不上报为错误态;
 * - 选择稳定性不在本 hook:面板按稳定行 key 维持选中(§4.3.1)。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { MeshApiClient } from '../../api/client';
import { MeshApiError } from '../../api/errors';
import { searchWorkspace } from './api';
import type { SearchResultItem } from './types';

/** 默认防抖窗口(§4.7) */
export const SEARCH_DEBOUNCE_MS = 150;

/** 完整 identifier 形态(§2.2:canonical uppercase 规范化等值在小写侧不敏感) */
export const IDENTIFIER_QUERY_PATTERN = /^\s*[a-zA-Z0-9]+-\d+\s*$/;

export interface UseEntitySearchOptions {
  readonly client: MeshApiClient;
  /** 工作区标识(路径 scope,§3.1);空串视为未就绪,不发请求 */
  readonly workspaceId: string;
  readonly query: string;
  /** 面板关闭等场景停检索;缺省 true */
  readonly enabled?: boolean;
  readonly debounceMs?: number;
}

export interface EntitySearchState {
  readonly entityResults: readonly SearchResultItem[];
  readonly loading: boolean;
  readonly error: MeshApiError | null;
  /** 错误态手动重试(不改变 query 即重发) */
  readonly retry: () => void;
}

export function useEntitySearch(options: UseEntitySearchOptions): EntitySearchState {
  const { client, workspaceId, query, enabled = true, debounceMs = SEARCH_DEBOUNCE_MS } = options;
  const [entityResults, setEntityResults] = useState<readonly SearchResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<MeshApiError | null>(null);
  const [retryTick, setRetryTick] = useState(0);
  const epochRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);

  const retry = useCallback(() => {
    setRetryTick((tick) => tick + 1);
  }, []);

  useEffect(() => {
    const trimmed = query.trim();
    if (!enabled || workspaceId === '' || trimmed === '') {
      epochRef.current += 1;
      controllerRef.current?.abort();
      controllerRef.current = null;
      setEntityResults([]);
      setLoading(false);
      setError(null);
      return;
    }

    const epoch = ++epochRef.current;

    const startRequest = (): void => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      setLoading(true);
      setError(null);
      void searchWorkspace(client, workspaceId, { q: trimmed, signal: controller.signal })
        .then((envelope) => {
          if (epochRef.current !== epoch) return; // 迟到响应丢弃(竞态治理)
          setEntityResults(envelope.data);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (epochRef.current !== epoch) return;
          if (controller.signal.aborted) return; // 过期取消:不上报错误态
          if (err instanceof MeshApiError) {
            setError(err);
          } else {
            setError(
              new MeshApiError({ status: 0, code: 'network', message: 'search failed' }),
            );
          }
          setLoading(false);
        });
    };

    // identifier 形态跳过防抖:同步即刻请求(§2.2 快路径,命中即顶置)
    if (IDENTIFIER_QUERY_PATTERN.test(trimmed)) {
      startRequest();
      return () => {
        controllerRef.current?.abort();
      };
    }

    const timer = setTimeout(startRequest, debounceMs);
    return () => {
      clearTimeout(timer);
      controllerRef.current?.abort();
    };
  }, [client, workspaceId, query, enabled, debounceMs, retryTick]);

  return { entityResults, loading, error, retry };
}
