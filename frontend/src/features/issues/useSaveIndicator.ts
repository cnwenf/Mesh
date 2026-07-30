/**
 * Issue 详情保存态指示纯 hook(design-quality.md §3.2 详情行:保存与冲突状态清楚)。
 *
 * 状态机:idle → saving → saved / conflict;saved 与 conflict 为弱提示,
 * FADE 后自动回落 idle(失败不落此机,仍走既有 danger toast + 字段内联错误)。
 * 计时器在状态迁移与卸载时清理,避免 setState-after-unmount。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export type SavePhase = 'idle' | 'saving' | 'saved' | 'conflict';

/** saved/conflict 弱提示自动淡出时长(毫秒)。 */
export const SAVE_INDICATOR_FADE_MS = 3000;

export interface SaveIndicator {
  readonly phase: SavePhase;
  /** 最近一次保存成功的时间戳(ISO);用于「已保存」相对时间呈现 */
  readonly savedAt: string | null;
  /** 进入 saving(清旧计时器) */
  readonly begin: () => void;
  /** 保存成功;可注入时间戳便于测试,缺省取当前时间 */
  readonly succeed: (timestamp?: string) => void;
  /** 409 收敛后:已被他人更新,已载入最新版本 */
  readonly conflict: () => void;
  /** 立即复位(如切换对象) */
  readonly reset: () => void;
}

export function useSaveIndicator(): SaveIndicator {
  const [phase, setPhase] = useState<SavePhase>('idle');
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => clearTimer, [clearTimer]);

  const scheduleFade = useCallback(() => {
    clearTimer();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setPhase('idle');
    }, SAVE_INDICATOR_FADE_MS);
  }, [clearTimer]);

  const begin = useCallback(() => {
    clearTimer();
    setPhase('saving');
  }, [clearTimer]);

  const succeed = useCallback(
    (timestamp?: string) => {
      setSavedAt(timestamp ?? new Date().toISOString());
      setPhase('saved');
      scheduleFade();
    },
    [scheduleFade],
  );

  const conflict = useCallback(() => {
    setPhase('conflict');
    scheduleFade();
  }, [scheduleFade]);

  const reset = useCallback(() => {
    clearTimer();
    setPhase('idle');
  }, [clearTimer]);

  return { phase, savedAt, begin, succeed, conflict, reset };
}
