/**
 * 页面模板 · DataView 键盘行选择(design-quality.md §3.2:键盘上下选择;§10.2
 * 键盘可完成筛选/选择)。
 *
 * 游标焦点(roving tabindex)模型:容器内行经 data-list-item-index 登记序号,
 * 仅 activeIndex 行 tabIndex=0。↑/↓(或 ←/→)移动活动行并真实移焦,Home/End
 * 跳首尾,Enter 打开,onToggle 由空格触发(多选)。不依赖任何行 DOM 结构,
 * 供表格行与主次行卡片共用。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface UseListKeyboardSelectionOptions {
  /** 行总数(随数据变化) */
  readonly itemCount: number;
  /** Enter 打开当前行(行主操作,§7.6) */
  readonly onOpen?: (index: number) => void;
  /** 空格切换选中(提供时启用多选语义) */
  readonly onToggle?: (index: number) => void;
}

export interface ListKeyboardSelection {
  /** 当前活动行序号;无活动行为 -1 */
  readonly activeIndex: number;
  /** 编程式设置活动行并移焦(如点击行后同步) */
  readonly activate: (index: number) => void;
  /** 行容器 ref(限定焦点查询范围) */
  readonly containerRef: React.RefObject<HTMLDivElement | null>;
  /** 行 onKeyDown:传入事件与本行序号 */
  readonly handleItemKeyDown: (event: React.KeyboardEvent, index: number) => void;
  /** 行 tabIndex:活动行 0,其余 -1(无活动行时首行 0) */
  readonly itemTabIndex: (index: number) => number;
}

export function useListKeyboardSelection(
  options: UseListKeyboardSelectionOptions,
): ListKeyboardSelection {
  const { itemCount, onOpen, onToggle } = options;
  const [activeIndex, setActiveIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const callbacksRef = useRef({ onOpen, onToggle });
  callbacksRef.current = { onOpen, onToggle };

  // 数据收缩导致活动行越界时回退到末行,保证 tabIndex 序列恒有 0 项。
  const clamped = activeIndex >= itemCount ? -1 : activeIndex;

  const focusItem = useCallback((index: number) => {
    const container = containerRef.current;
    if (container === null) return;
    const target = container.querySelector<HTMLElement>(`[data-list-item-index="${index}"]`);
    target?.focus();
  }, []);

  const activate = useCallback(
    (index: number) => {
      if (index < 0 || index >= itemCount) return;
      setActiveIndex(index);
      focusItem(index);
    },
    [itemCount, focusItem],
  );

  // 活动行变化后真实移焦(键盘路径与编程路径统一)。
  const lastFocused = useRef(-1);
  useEffect(() => {
    if (clamped >= 0 && clamped !== lastFocused.current) {
      lastFocused.current = clamped;
      focusItem(clamped);
    }
  }, [clamped, focusItem]);

  const move = useCallback(
    (from: number, delta: number) => {
      const base = from < 0 ? (delta > 0 ? -1 : itemCount) : from;
      const next = Math.min(itemCount - 1, Math.max(0, base + delta));
      setActiveIndex(next);
      focusItem(next);
    },
    [itemCount, focusItem],
  );

  const handleItemKeyDown = useCallback(
    (event: React.KeyboardEvent, index: number) => {
      const vertical = event.key === 'ArrowDown' || event.key === 'ArrowUp';
      const horizontal = event.key === 'ArrowRight' || event.key === 'ArrowLeft';
      if (vertical || horizontal) {
        event.preventDefault();
        const forward = event.key === 'ArrowDown' || event.key === 'ArrowRight';
        move(index, forward ? 1 : -1);
        return;
      }
      if (event.key === 'Home') {
        event.preventDefault();
        move(-1, 1);
        return;
      }
      if (event.key === 'End') {
        event.preventDefault();
        move(itemCount, -1);
        return;
      }
      if (event.key === 'Enter') {
        callbacksRef.current.onOpen?.(index);
        return;
      }
      if (event.key === ' ') {
        if (callbacksRef.current.onToggle !== undefined) {
          event.preventDefault();
          callbacksRef.current.onToggle(index);
        }
      }
    },
    [move, itemCount],
  );

  const itemTabIndex = useCallback(
    (index: number): number => (clamped === -1 ? (index === 0 ? 0 : -1) : index === clamped ? 0 : -1),
    [clamped],
  );

  return { activeIndex: clamped, activate, containerRef, handleItemKeyDown, itemTabIndex };
}
