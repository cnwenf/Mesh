/**
 * 对话框:role=dialog + aria-modal,由 title prop 标注(aria-labelledby)。
 * 焦点圈养:打开后焦点移入;Tab/Shift+Tab 在对话框内循环;Esc 关闭(onClose);
 * 点击遮罩关闭;关闭后焦点归还打开前的触发元素。无硬编码文案(closeLabel 来自调用方)。
 *
 * 弹层分层关闭栈(§4.5,评审 P3):打开期间经 overlayStack 登记——快捷键分发
 * 据此全屏蔽背景页面键;Esc 语义分层:弹层内输入控件获焦时首个 Esc 仅失焦
 * 输入控件,不关弹层;关闭后焦点归还触发元素,触发元素已不存在时回落页面
 * 主区域首个可聚焦元素(绝不落 body)。Tab/Shift+Tab 圈养经 trapTabKey 与
 * Drawer/Menu 共用同一实现(design-quality §7.5/§10.2,杜绝多实现漂移)。
 */
import { useEffect, useId, useRef } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, ReactNode } from 'react';
import {
  isFormFieldElement,
  pushOverlay,
  restoreOverlayFocus,
} from '../../shortcuts/overlayStack';
import { IconButton } from './IconButton';
import { trapTabKey } from './useFocusTrap';
import './components.css';

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  /** 对话框标题(可访问名来源) */
  title: string;
  /** 关闭按钮可访问名;提供时渲染关闭按钮(鼠标等价路径) */
  closeLabel?: string;
  children: ReactNode;
}

export function Dialog(props: DialogProps): React.JSX.Element | null {
  const { open, onClose, title, closeLabel, children } = props;
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  // §4.5 分层关闭:登记 overlayStack(快捷键分发全屏蔽背景键)并于关闭时归还
  // 焦点;触发元素已不存在时经 restoreOverlayFocus 回落 main 首个可聚焦元素。
  // 焦点圈养不复用 useFocusTrap 整体钩子:其归还路径无 overlayStack 登记与
  // 触发元素失效回落(§6.12 绝不落 body),Tab 圈养已经共享 trapTabKey。
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previouslyFocusedRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    const removeOverlay = pushOverlay({
      id: titleId,
      returnFocusTo: previouslyFocusedRef.current,
    });
    return () => {
      removeOverlay();
      // 触发元素已不在文档中时回落 main 首个可聚焦元素(§6.12,绝不落 body)。
      restoreOverlayFocus(previouslyFocusedRef.current);
    };
  }, [open, titleId]);

  if (!open) {
    return null;
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      // §4.5 分层关闭:弹层内输入控件获焦时,首个 Esc 仅失焦输入控件,不关弹层。
      const active = document.activeElement;
      if (
        active !== null &&
        active !== dialogRef.current &&
        isFormFieldElement(active) &&
        dialogRef.current?.contains(active) === true
      ) {
        (active as HTMLElement).blur();
        return;
      }
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const root = dialogRef.current;
    if (!root) return;
    if (trapTabKey(root, event.shiftKey)) {
      event.preventDefault();
    }
  };

  const handleBackdropMouseDown = (event: ReactMouseEvent<HTMLDivElement>): void => {
    if (event.target === event.currentTarget) {
      onClose();
    }
  };

  return (
    <div className="mesh-dialog__backdrop" onMouseDown={handleBackdropMouseDown}>
      <div
        ref={dialogRef}
        className="mesh-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <div className="mesh-dialog__header">
          <h2 id={titleId} className="mesh-dialog__title">
            {title}
          </h2>
          {closeLabel !== undefined ? (
            <IconButton label={closeLabel} className="mesh-dialog__close" onClick={onClose}>
              ×
            </IconButton>
          ) : null}
        </div>
        <div className="mesh-dialog__body">{children}</div>
      </div>
    </div>
  );
}
