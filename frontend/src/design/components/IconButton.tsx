/**
 * 图标按钮:`label` 必填 → aria-label(图标内容 aria-hidden,读屏仅朗读 label)。
 */
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { forwardRef } from 'react';
import type { ButtonSize, ButtonVariant } from './Button';
import { Button } from './Button';
import './components.css';

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** 可访问名(必填):图标按钮没有可见文本,必须经 aria-label 提供 */
  label: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** 图标内容(对读屏隐藏) */
  children: ReactNode;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, variant = 'ghost', size = 'md', type = 'button', className, children, ...rest },
  ref,
) {
  const classes = ['mesh-icon-button', `mesh-icon-button--${size}`, className]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  return (
    <Button
      ref={ref}
      type={type}
      variant={variant}
      size={size}
      className={classes}
      aria-label={label}
      {...rest}
    >
      <span className="mesh-icon-button__icon" aria-hidden="true">
        {children}
      </span>
    </Button>
  );
});
