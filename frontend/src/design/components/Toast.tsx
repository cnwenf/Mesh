/**
 * Toast(异常态矩阵 retry 行的轻量反馈):Provider + useToast + aria-live=polite live region。
 *
 * - 无硬编码可见文案:regionLabel(区域名)、closeLabel(关闭按钮名)均由调用方提供;
 * - 自动消失(默认 DEFAULT_TOAST_DURATION_MS,可注入计时器测试)与手动关闭(同时取消挂起计时器);
 * - 状态不可变更新;卸载清理全部挂起计时器。
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { IconButton } from './IconButton';
import './components.css';

/** Toast 自动消失默认时长(ms) */
export const DEFAULT_TOAST_DURATION_MS = 5000;

export type ToastTone = 'info' | 'success' | 'warn' | 'danger';

/** 可注入计时器(测试以同步桩替代真实定时器) */
export interface ToastTimer {
  setTimeout: (handler: () => void, timeoutMs: number) => number;
  clearTimeout: (handle: number) => void;
}

const defaultTimer: ToastTimer = {
  setTimeout: (handler, timeoutMs) => window.setTimeout(handler, timeoutMs),
  clearTimeout: (handle) => {
    window.clearTimeout(handle);
  },
};

export interface ToastItem {
  id: string;
  tone: ToastTone;
  message: string;
  /** 关闭按钮可访问名(调用方提供) */
  closeLabel: string;
  actionLabel?: string;
  onAction?: () => void;
}

export interface ToastOptions {
  tone?: ToastTone;
  /** 覆盖自动消失时长(ms) */
  durationMs?: number;
  /** 关闭按钮可访问名(必填,无默认文案) */
  closeLabel: string;
  actionLabel?: string;
  onAction?: () => void;
}

export interface ToastContextValue {
  toasts: ReadonlyArray<ToastItem>;
  addToast: (message: string, options: ToastOptions) => string;
  dismissToast: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a <ToastProvider>.');
  }
  return context;
}

export interface ToastProviderProps {
  children: ReactNode;
  /** live region 可访问名(必填,无默认文案) */
  regionLabel: string;
  /** 注入计时器(测试用);缺省走 window.setTimeout */
  timer?: ToastTimer;
}

let nextToastId = 0;
function createToastId(): string {
  nextToastId += 1;
  return `mesh-toast-${nextToastId}`;
}

export function ToastProvider(props: ToastProviderProps): React.JSX.Element {
  const { children, regionLabel, timer = defaultTimer } = props;
  const [toasts, setToasts] = useState<ReadonlyArray<ToastItem>>([]);
  const handlesRef = useRef<Map<string, number>>(new Map());

  const dismissToast = useCallback(
    (id: string): void => {
      const handle = handlesRef.current.get(id);
      if (handle !== undefined) {
        timer.clearTimeout(handle);
        handlesRef.current.delete(id);
      }
      setToasts((previous) => previous.filter((toast) => toast.id !== id));
    },
    [timer],
  );

  const addToast = useCallback(
    (message: string, options: ToastOptions): string => {
      const id = createToastId();
      const item: ToastItem = {
        id,
        tone: options.tone ?? 'info',
        message,
        closeLabel: options.closeLabel,
        actionLabel: options.actionLabel,
        onAction: options.onAction,
      };
      setToasts((previous) => [...previous, item]);
      const handle = timer.setTimeout(
        () => dismissToast(id),
        options.durationMs ?? DEFAULT_TOAST_DURATION_MS,
      );
      handlesRef.current.set(id, handle);
      return id;
    },
    [timer, dismissToast],
  );

  useEffect(
    () => () => {
      for (const handle of handlesRef.current.values()) {
        timer.clearTimeout(handle);
      }
      handlesRef.current.clear();
    },
    [timer],
  );

  const contextValue: ToastContextValue = { toasts, addToast, dismissToast };

  return (
    <ToastContext.Provider value={contextValue}>
      {children}
      <div className="mesh-toast-region" role="status" aria-live="polite" aria-label={regionLabel}>
        {toasts.map((toast) => (
          <div key={toast.id} className={`mesh-toast mesh-toast--${toast.tone}`}>
            <span className="mesh-toast__message">{toast.message}</span>
            {toast.actionLabel !== undefined && toast.onAction !== undefined ? (
              <button
                type="button"
                className="mesh-toast__action"
                onClick={() => {
                  toast.onAction?.();
                  dismissToast(toast.id);
                }}
              >
                {toast.actionLabel}
              </button>
            ) : null}
            <IconButton
              label={toast.closeLabel}
              className="mesh-toast__close"
              onClick={() => dismissToast(toast.id)}
            >
              ×
            </IconButton>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
