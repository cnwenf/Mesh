/**
 * keyset 游标分页 — 权威:docs/specs/README.md §6.14(next_cursor=null 为末页)。
 * useCursorPagination:首屏自动加载、跨页累积、去重并发、错误态、重置。
 * fetchAllPages:非 React 辅助,沿游标走到 null(供对账器/测试使用)。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ListEnvelope } from '../types/envelopes';
import { MeshApiError } from './errors';

export interface CursorPage<T> {
  items: readonly T[];
  isLoading: boolean;
  isFetchingNext: boolean;
  error: MeshApiError | null;
  hasMore: boolean;
  fetchNext(): Promise<void>;
  reset(): Promise<void>;
}

type LoadMode = 'initial' | 'next';

function toApiError(err: unknown): MeshApiError {
  return err instanceof MeshApiError
    ? err
    : new MeshApiError({ status: 0, code: 'network', message: 'network error' });
}

export function useCursorPagination<T>(
  fetcher: (cursor: string | null) => Promise<ListEnvelope<T>>,
): CursorPage<T> {
  const [items, setItems] = useState<T[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isFetchingNext, setIsFetchingNext] = useState(false);
  const [error, setError] = useState<MeshApiError | null>(null);
  const [hasMore, setHasMore] = useState(true);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const cursorRef = useRef<string | null>(null);
  const hasMoreRef = useRef(true);
  const initialLoadingRef = useRef(true);
  const fetchingNextRef = useRef(false);
  // reset 自增 epoch,使在途的过期结果被丢弃(不污染重置后的新列表)。
  const epochRef = useRef(0);

  const loadPage = useCallback(async (cursor: string | null, mode: LoadMode): Promise<void> => {
    const epoch = epochRef.current;
    try {
      const page = await fetcherRef.current(cursor);
      if (epochRef.current !== epoch) return;
      setItems((prev) => (mode === 'initial' ? [...page.data] : [...prev, ...page.data]));
      cursorRef.current = page.next_cursor;
      hasMoreRef.current = page.next_cursor !== null;
      setHasMore(page.next_cursor !== null);
      setError(null);
    } catch (err) {
      if (epochRef.current !== epoch) return;
      setError(toApiError(err));
      hasMoreRef.current = false;
      setHasMore(false);
    } finally {
      // 过期结果不触碰加载标记(由发起方/reset 负责复位)。
      if (epochRef.current === epoch) {
        if (mode === 'initial') {
          initialLoadingRef.current = false;
          setIsLoading(false);
        } else {
          fetchingNextRef.current = false;
          setIsFetchingNext(false);
        }
      }
    }
  }, []);

  const fetchNext = useCallback(async (): Promise<void> => {
    if (fetchingNextRef.current || initialLoadingRef.current || !hasMoreRef.current) {
      return;
    }
    fetchingNextRef.current = true;
    setIsFetchingNext(true);
    await loadPage(cursorRef.current, 'next');
  }, [loadPage]);

  const reset = useCallback(async (): Promise<void> => {
    epochRef.current += 1;
    cursorRef.current = null;
    hasMoreRef.current = true;
    initialLoadingRef.current = true;
    fetchingNextRef.current = false;
    setItems([]);
    setHasMore(true);
    setIsLoading(true);
    setIsFetchingNext(false);
    setError(null);
    await loadPage(null, 'initial');
  }, [loadPage]);

  useEffect(() => {
    void loadPage(null, 'initial');
  }, [loadPage]);

  return { items, isLoading, isFetchingNext, error, hasMore, fetchNext, reset };
}

/** 沿游标走到 null,聚合所有页(供对账器/测试)。 */
export async function fetchAllPages<T>(
  fetcher: (cursor: string | null) => Promise<ListEnvelope<T>>,
): Promise<T[]> {
  const all: T[] = [];
  let cursor: string | null = null;
  do {
    const page = await fetcher(cursor);
    all.push(...page.data);
    cursor = page.next_cursor;
  } while (cursor !== null);
  return all;
}
