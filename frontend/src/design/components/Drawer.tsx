/**
 * 抽屉(design-quality.md §7.5:查看/编辑当前页次级上下文,如 issue 属性、筛选器):
 * - role=dialog + aria-modal,由 title 标注;
 * - 焦点圈养 + Esc 关闭 + 遮罩关闭 + 关闭归还焦点(useFocusTrap/trapTabKey);
 * - 桌面右侧滑入;0–599px 自动转底部 sheet(CSS,考虑安全区)。
 * 无硬编码可见文案(closeLabel 来自调用方)。
 */
import { useRef } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, ReactNode } from 'react';
import { IconButton } from './IconButton';
import { trapTabKey, useFocusTrap } from './useFocusTrap';
import './overlays.css';

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  /** 抽屉标题(可访问名来源) */
  title: string;
  /** 关闭按钮可访问名;提供时渲染关闭按钮(鼠标/触控等价路径) */
  closeLabel?: string;
  /** 底部操作区插槽(保存/取消等) */
  footer?: ReactNode;
  children: ReactNode;
}

export function Drawer(props: DrawerProps): React.JSX.Element | null {
  const { open, onClose, title, closeLabel, footer, children } = props;
  const drawerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(open, drawerRef);

  if (!open) {
    return null;
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    // 打开态下 ref 必然已挂载(handleKeyDown 仅来自已渲染的抽屉)
    const root = drawerRef.current!;
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
    <div className="mesh-drawer__backdrop" onMouseDown={handleBackdropMouseDown}>
      <div
        ref={drawerRef}
        className="mesh-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <div className="mesh-drawer__header">
          <h2 className="mesh-drawer__title">{title}</h2>
          {closeLabel !== undefined ? (
            <IconButton label={closeLabel} className="mesh-drawer__close" onClick={onClose}>
              ×
            </IconButton>
          ) : null}
        </div>
        <div className="mesh-drawer__body">{children}</div>
        {footer !== undefined ? <div className="mesh-drawer__footer">{footer}</div> : null}
      </div>
    </div>
  );
}
