/**
 * 按钮(design-quality.md §7.3):variant(primary/secondary/ghost/danger)、
 * size(sm 28 / md 36 / lg 44,触控/认证场景用 lg)、isLoading。
 * 状态矩阵完整:default/hover/focus-visible/pressed/loading/disabled(components.css)。
 * 原生 <button> 语义(Enter/Space 激活免费获得);isLoading 时禁用 + aria-busy,
 * 可访问名保持不变(子内容仍在,仅叠加 aria-hidden spinner)。
 * 无硬编码可见文案。
 */
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { forwardRef } from 'react';
import './components.css';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** 加载中:禁用 + spinner + aria-busy,可访问名不变 */
  isLoading?: boolean;
  children: ReactNode;
}

export function buttonClasses(
  variant: ButtonVariant,
  size: ButtonSize,
  extraClassName?: string,
): string {
  return ['mesh-button', `mesh-button--${variant}`, `mesh-button--${size}`, extraClassName]
    .filter((part): part is string => Boolean(part))
    .join(' ');
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', isLoading = false, type = 'button', disabled, className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={buttonClasses(variant, size, className)}
      disabled={disabled || isLoading}
      aria-busy={isLoading ? true : undefined}
      {...rest}
    >
      {isLoading ? <span className="mesh-button__spinner" aria-hidden="true" /> : null}
      <span className="mesh-button__label">{children}</span>
    </button>
  );
});
