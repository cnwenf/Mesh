/**
 * 浮层焦点圈养(design-quality.md §7.5/§10.2):打开后焦点移入容器,
 * Tab/Shift+Tab 在容器内循环,关闭后焦点归还打开前的触发元素。
 * Dialog/Drawer/Menu 共用同一实现,避免焦点管理多实现漂移。
 *
 * @param open 浮层可见性
 * @param containerRef 浮层根节点
 * @param restoreFocus 关闭时是否归还焦点(默认 true)
 */
import { useEffect } from 'react';
import type { RefObject } from 'react';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

/** 容器内可聚焦元素(按 DOM 顺序)。 */
export function focusableElements(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

/**
 * 处理一次 Tab 键事件:在容器内循环焦点。
 * 无焦点元素时阻止默认并把焦点留在容器本身。
 * 返回 true 表示事件已被处理(调用方可据此停止冒泡)。
 */
export function trapTabKey(root: HTMLElement, shiftKey: boolean): boolean {
  const focusables = focusableElements(root);
  if (focusables.length === 0) {
    root.focus();
    return true;
  }
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = document.activeElement;
  if (shiftKey && (active === first || active === root)) {
    last.focus();
    return true;
  }
  if (!shiftKey && active === last) {
    first.focus();
    return true;
  }
  return false;
}

export function useFocusTrap(
  open: boolean,
  containerRef: RefObject<HTMLElement | null>,
  restoreFocus = true,
): void {
  useEffect(() => {
    if (!open) return;
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    containerRef.current?.focus();
    return () => {
      if (restoreFocus) {
        previouslyFocused?.focus();
      }
    };
  }, [open, containerRef, restoreFocus]);
}
