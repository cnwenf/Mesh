/**
 * useUrlState(L92 URL 状态同步):把单个 search param 当状态用 ——
 * 读时缺省为 null,写 null/空串即删键;默认 replace 写入,不污染历史栈,
 * 保留其余键。页面级合法值校验由调用方完成(见 InboxPage/IssueDetailPage)。
 */
import { useCallback } from 'react';
import { useSearchParams } from 'react-router';

export interface UrlStateOptions {
  /** 写入方式:replace(默认)不产生历史条目;push 可回退。 */
  readonly mode?: 'replace' | 'push';
}

export function useUrlState(
  key: string,
): [string | null, (value: string | null, options?: UrlStateOptions) => void] {
  const [searchParams, setSearchParams] = useSearchParams();
  const value = searchParams.get(key);

  const setValue = useCallback(
    (next: string | null, options?: UrlStateOptions) => {
      setSearchParams(
        (current) => {
          const params = new URLSearchParams(current);
          if (next === null || next === '') params.delete(key);
          else params.set(key, next);
          return params;
        },
        { replace: options?.mode !== 'push' },
      );
    },
    [key, setSearchParams],
  );

  return [value, setValue];
}
