/**
 * 对话框:role=dialog + aria-modal,由 title prop 标注(aria-labelledby)。
 * 焦点圈养:打开后焦点移入;Tab/Shift+Tab 在对话框内循环;Esc 关闭(onClose);
 * 点击遮罩关闭;关闭后焦点归还打开前的触发元素(与 Drawer/Menu 共用 useFocusTrap)。
 * 无硬编码文案(closeLabel 来自调用方)。
 */
import { useId, useEffect, useRef } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, ReactNode } from 'react';
import { IconButton } from './IconButton';
import { trapTabKey, useFocusTrap } from './useFocusTrap';
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
  useFocusTrap(open, dialogRef);

  // Esc 关闭经 document 监听:模态须「任意焦点位置」均可 Esc 关闭(§7.5/§10.2)。
  // 仅绑容器 onKeyDown 会在焦点意外落在 body 时漏接(焦点圈养竞态)→ 弹层关不掉。
  useEffect(() => {
    if (!open) return;
    const onEscape = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onEscape);
    return () => document.removeEventListener('keydown', onEscape);
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
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
