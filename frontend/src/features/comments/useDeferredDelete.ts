/**
 * 延迟删除状态机(design-quality.md §9.5.5「删除可短时撤销」)。
 * 调用方先做乐观隐藏,再 request(item) 开启撤销窗口:
 *   pending(窗口内,可 undo)→ committing(窗口到期调用 commit)→ committed(成功)
 *   commit 失败 → failed(经 onFailed 由调用方回滚 + 危险提示)。
 *
 * - 窗口内 undo():取消挂起删除,返回 true;已 committing/committed 返回 false。
 * - 双重删除守卫:已有 pending 项时 request 被忽略(防重复计时/重复请求)。
 * - 计时器可注入,便于 fake-timers 彻底覆盖窗口前后与失败回滚。
 * 纯状态机:不做任何 DOM/网络副作用,commit/回滚/提示均由调用方注入。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

/** 撤销窗口(ms):§9.5.5 短时撤销,默认 5s。 */
export const UNDO_WINDOW_MS = 5000;

export type DeferredDeletePhase = 'pending' | 'committing' | 'committed' | 'failed';

export interface DeferredDeleteTimers {
  readonly setTimeout: (handler: () => void, ms: number) => number;
  readonly clearTimeout: (handle: number) => void;
}

const defaultTimers: DeferredDeleteTimers = {
  setTimeout: (handler, ms) => window.setTimeout(handler, ms),
  clearTimeout: (handle) => {
    window.clearTimeout(handle);
  },
};

export interface UseDeferredDeleteOptions<T> {
  /** 撤销窗口,缺省 UNDO_WINDOW_MS。 */
  readonly windowMs?: number;
  /** 窗口到期后的真实删除(网络副作用),reject 触发 onFailed。 */
  readonly commit: (item: T) => Promise<void>;
  /** 删除成功后回调(可选)。 */
  readonly onCommitted?: (item: T) => void;
  /** 删除失败后回调(调用方据此回滚 + 危险提示)。 */
  readonly onFailed?: (item: T, error: unknown) => void;
  /** 注入计时器(测试);缺省走 window。 */
  readonly timers?: DeferredDeleteTimers;
}

export interface DeferredDelete<T> {
  /** 当前阶段;无活动删除为 null。 */
  readonly phase: DeferredDeletePhase | null;
  /** 当前活动删除项;无则 null。 */
  readonly pending: T | null;
  /** 开启一次延迟删除;已有 pending 项时忽略(双重删除守卫)。 */
  readonly request: (item: T) => void;
  /** 窗口内撤销;true=成功撤销,false=已过期/无活动删除。 */
  readonly undo: () => boolean;
  /** 复位到空闲(失败回滚后清理用)。 */
  readonly reset: () => void;
}

export function useDeferredDelete<T>(options: UseDeferredDeleteOptions<T>): DeferredDelete<T> {
  const windowMs = options.windowMs ?? UNDO_WINDOW_MS;
  const timers = options.timers ?? defaultTimers;
  const [phase, setPhase] = useState<DeferredDeletePhase | null>(null);
  const [pending, setPending] = useState<T | null>(null);
  // 以 ref 镜像最新 commit/回调,避免其身份变化重开窗口。
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const handleRef = useRef<number | null>(null);
  const pendingRef = useRef<T | null>(null);
  const phaseRef = useRef<DeferredDeletePhase | null>(null);
  pendingRef.current = pending;
  phaseRef.current = phase;

  const clearTimer = useCallback((): void => {
    if (handleRef.current !== null) {
      timers.clearTimeout(handleRef.current);
      handleRef.current = null;
    }
  }, [timers]);

  const reset = useCallback((): void => {
    clearTimer();
    setPending(null);
    setPhase(null);
  }, [clearTimer]);

  const commitNow = useCallback(
    (item: T): void => {
      setPhase('committing');
      void optionsRef.current
        .commit(item)
        .then(() => {
          setPhase('committed');
          setPending(null);
          optionsRef.current.onCommitted?.(item);
          // committed 为终态,随后归零以允许下一次删除。
          setPhase(null);
        })
        .catch((error: unknown) => {
          setPhase('failed');
          setPending(null);
          optionsRef.current.onFailed?.(item, error);
          setPhase(null);
        });
    },
    [],
  );

  const request = useCallback(
    (item: T): void => {
      // 双重删除守卫:已有活动删除则忽略。
      if (pendingRef.current !== null) return;
      setPending(item);
      setPhase('pending');
      clearTimer();
      handleRef.current = timers.setTimeout(() => {
        handleRef.current = null;
        commitNow(item);
      }, windowMs);
    },
    [clearTimer, commitNow, timers, windowMs],
  );

  const undo = useCallback((): boolean => {
    if (phaseRef.current !== 'pending' || pendingRef.current === null) return false;
    clearTimer();
    setPending(null);
    setPhase(null);
    return true;
  }, [clearTimer]);

  // 卸载清理挂起计时器,杜绝卸载后 setState。
  useEffect(() => clearTimer, [clearTimer]);

  return { phase, pending, request, undo, reset };
}
