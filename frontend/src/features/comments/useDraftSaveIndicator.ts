/**
 * 草稿自动保存弱提示状态机(design-quality.md §9.5.1)。
 * useCommentDraft 已写穿 localStorage(同步持久化);本 hook 只负责把「正在输入」
 * 映射为可读的弱提示状态:dirty(刚改动)→ saving(防抖 ~600ms 后)→ saved(落定)。
 *
 * - 初次挂载不触发(值来自草稿恢复而非用户输入,无需提示「保存中」);
 * - 值未变化不触发(依赖数组只在 value 变化时重跑);
 * - 清空('')回到 idle,不再提示;
 * - 计时器/时钟可注入,便于 fake-timers 单测防抖时序。
 */
import { useEffect, useRef, useState } from 'react';

export type DraftSaveStatus = 'idle' | 'dirty' | 'saving' | 'saved';

/** 防抖窗口(ms):停止输入约 0.6s 后进入 saving。 */
export const DRAFT_SAVE_DEBOUNCE_MS = 600;
/** saving 视觉停留(ms):写穿是同步的,这里仅作短暂过渡再落 saved。 */
export const DRAFT_SAVE_SAVING_MS = 200;

export interface DraftSaveTimers {
  readonly setTimeout: (handler: () => void, ms: number) => number;
  readonly clearTimeout: (handle: number) => void;
}

const defaultTimers: DraftSaveTimers = {
  setTimeout: (handler, ms) => window.setTimeout(handler, ms),
  clearTimeout: (handle) => {
    window.clearTimeout(handle);
  },
};

export interface UseDraftSaveIndicatorOptions {
  /** 防抖窗口,缺省 DRAFT_SAVE_DEBOUNCE_MS。 */
  readonly debounceMs?: number;
  /** saving 过渡时长,缺省 DRAFT_SAVE_SAVING_MS。 */
  readonly savingMs?: number;
  /** 注入计时器(测试);缺省走 window。 */
  readonly timers?: DraftSaveTimers;
  /** 注入时钟(测试);缺省 Date.now。 */
  readonly now?: () => number;
}

export interface DraftSaveIndicator {
  readonly status: DraftSaveStatus;
  /** 最近一次 saved 的纪元毫秒;从未保存为 null。 */
  readonly savedAt: number | null;
}

/**
 * 依据草稿当前值产出弱提示状态。仅在 value 真正变化时推进状态机;
 * 连续输入会重置防抖,最终稳定在 saved。
 */
export function useDraftSaveIndicator(
  value: string,
  options: UseDraftSaveIndicatorOptions = {},
): DraftSaveIndicator {
  const debounceMs = options.debounceMs ?? DRAFT_SAVE_DEBOUNCE_MS;
  const savingMs = options.savingMs ?? DRAFT_SAVE_SAVING_MS;
  const timers = options.timers ?? defaultTimers;
  const now = options.now ?? Date.now;
  const [status, setStatus] = useState<DraftSaveStatus>('idle');
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const isFirst = useRef(true);
  const savingHandle = useRef<number | null>(null);

  useEffect(() => {
    // 初次挂载:值来自草稿恢复,不提示「保存中」。
    if (isFirst.current) {
      isFirst.current = false;
      return;
    }
    if (value === '') {
      setStatus('idle');
      return;
    }
    setStatus('dirty');
    const debounceHandle = timers.setTimeout(() => {
      setStatus('saving');
      savingHandle.current = timers.setTimeout(() => {
        setSavedAt(now());
        setStatus('saved');
        savingHandle.current = null;
      }, savingMs);
    }, debounceMs);
    return () => {
      timers.clearTimeout(debounceHandle);
      if (savingHandle.current !== null) {
        timers.clearTimeout(savingHandle.current);
        savingHandle.current = null;
      }
    };
    // 仅在 value/配置变化时推进;timers/now 由调用方保持稳定。
  }, [value, debounceMs, savingMs, timers, now]);

  return { status, savedAt };
}
