/**
 * 乐观更新 + 服务端版本校验 + 409 收敛 — 权威:docs/specs/README.md §6.14、§3.2。
 * PATCH 携带 `If-Match: <updated_at>`;冲突(409 conflict)时重拉服务端最新,
 * 由 onConflict 收敛,或以最新版本重放一次;二次冲突上抛。
 */
import { useCallback, useRef, useState } from 'react';
import type { MeshApiClient } from './client';
import { MeshApiError } from './errors';

export interface OptimisticPlan<T> {
  /** 本地当前值(带 updated_at/version) */
  current: T;
  changes: Partial<T>;
  /** 取服务端版本(通常为 updated_at) */
  getServerVersion: (v: T) => string;
}

export interface OptimisticResult<T> {
  result: T;
  conflicted: boolean;
}

function isConflict(err: unknown): err is MeshApiError {
  return err instanceof MeshApiError && err.status === 409 && err.code === 'conflict';
}

export async function optimisticUpdate<T>(
  client: MeshApiClient,
  path: string,
  plan: OptimisticPlan<T>,
  onConflict?: (server: T, err: MeshApiError) => Promise<T>,
): Promise<OptimisticResult<T>> {
  try {
    const result = await client.request<T>('PATCH', path, {
      body: plan.changes,
      ifMatch: plan.getServerVersion(plan.current),
    });
    return { result, conflicted: false };
  } catch (err) {
    if (!isConflict(err)) {
      throw err;
    }
    const server = await client.request<T>('GET', path);
    if (onConflict) {
      const result = await onConflict(server, err);
      return { result, conflicted: true };
    }
    // 无收敛回调:以服务端最新版本重放一次;二次冲突(或其他错误)自然上抛。
    const result = await client.request<T>('PATCH', path, {
      body: plan.changes,
      ifMatch: plan.getServerVersion(server),
    });
    return { result, conflicted: true };
  }
}

export interface UseOptimisticMutationOptions<T> {
  client: MeshApiClient;
  path: string;
  getServerVersion: (v: T) => string;
  onConflict?: (server: T, err: MeshApiError) => Promise<T>;
}

export interface OptimisticMutation<T> {
  mutate: (current: T, changes: Partial<T>) => Promise<OptimisticResult<T>>;
  isMutating: boolean;
  lastError: MeshApiError | null;
}

function toApiError(err: unknown): MeshApiError {
  return err instanceof MeshApiError
    ? err
    : new MeshApiError({ status: 0, code: 'network', message: 'network error' });
}

export function useOptimisticMutation<T>(
  opts: UseOptimisticMutationOptions<T>,
): OptimisticMutation<T> {
  const [isMutating, setIsMutating] = useState(false);
  const [lastError, setLastError] = useState<MeshApiError | null>(null);

  const optsRef = useRef(opts);
  optsRef.current = opts;

  const mutate = useCallback(
    async (current: T, changes: Partial<T>): Promise<OptimisticResult<T>> => {
      setIsMutating(true);
      setLastError(null);
      try {
        const { client, path, getServerVersion, onConflict } = optsRef.current;
        return await optimisticUpdate(client, path, { current, changes, getServerVersion }, onConflict);
      } catch (err) {
        setLastError(toApiError(err));
        throw err;
      } finally {
        setIsMutating(false);
      }
    },
    [],
  );

  return { mutate, isMutating, lastError };
}
