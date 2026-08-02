/**
 * 实体搜索 hook(search-command-palette.md §4.7:防抖 150ms + 过期请求取消)。
 *
 * - 本地命令过滤在面板层同步进行(零延迟);本 hook 只负责服务端对象检索;
 * - 防抖:query 变化后等待 debounceMs 再发请求;完整 identifier 形态
 *   (`KEY-N`,大小写不敏感)跳过防抖即刻请求(§2.2 等值快路径,命中顶置);
 * - 竞态治理:每次发请求前 abort 上一在途请求(AbortController),且以单调代次
 *   守卫丢弃迟到响应;被 abort 的失败不上报为错误态;
 * - settled 语义:当前 query 的检索「已完成」——防抖窗口与在途请求均为 false,
 *   仅成功/失败落定(或空 query 无需请求)后为 true。面板据此门控 no-results:
 *   只有「已完成且结果空」才呈现,杜绝在途/防抖窗口的瞬态闪现(§4.2 loading 态覆盖);
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
  /**
   * 当前 query 的检索是否已完成:防抖窗口与在途请求期间为 false,成功/失败落定
   * (或空 query 无需请求)后为 true。面板门控 no-results 仅在「已完成且结果空」
   * 时呈现(§4.2),避免在途/防抖窗口瞬态闪现。
   */
  readonly settled: boolean;
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
  // 已完成检索的 query(裁剪态);null = 当前 query 尚未完成。query 一变即在渲染期
  // 失效(下方渲染期状态调整,先于提交,不产生中间帧),settled 据此派生。
  const [completedQuery, setCompletedQuery] = useState<string | null>('');
  const [trackedQuery, setTrackedQuery] = useState(query);
  if (trackedQuery !== query) {
    // 渲染期调整状态(React「props 变化时调整 state」模式):query 变化即视上一
    // 完成态过期,即使回到先前已完成的 query 也须待本轮检索完成方可再报 settled。
    setTrackedQuery(query);
    setCompletedQuery(null);
  }
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
          setCompletedQuery(trimmed);
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
          setCompletedQuery(trimmed); // 失败亦属「已完成」:错误态由 error 驱动呈现
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

  const trimmedNow = query.trim();
  const settled = trimmedNow === '' || completedQuery === trimmedNow;

  return { entityResults, loading, settled, error, retry };
}
