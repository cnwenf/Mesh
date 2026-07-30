/**
 * 文本输入:`label` 渲染 <label htmlFor>;error/hint 经 aria-describedby 关联;
 * error 存在时 aria-invalid。受控友好(value/onChange 透传)。无硬编码文案。
 * `size`:'md' 默认 36px;'lg' 44px(触控/认证场景,§7.4)。
 */
import type { InputHTMLAttributes } from 'react';
import { forwardRef, useId } from 'react';
import './components.css';

export type InputSize = 'md' | 'lg';

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  /** 可见标签(必填):渲染 <label htmlFor> */
  label: string;
  /** 错误文案插槽 */
  error?: string;
  /** 提示文案插槽 */
  hint?: string;
  /** 控件高度档:md 36px(默认)/ lg 44px(触控·认证,§7.4) */
  size?: InputSize;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, error, hint, size = 'md', id, className, ...rest },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? `mesh-input-${autoId}`;
  const errorId = `${inputId}-error`;
  const hintId = `${inputId}-hint`;
  const describedBy =
    [error ? errorId : null, hint ? hintId : null]
      .filter((part): part is string => Boolean(part))
      .join(' ') || undefined;

  const controlClasses = [
    'mesh-field__control',
    size === 'lg' ? 'mesh-field__control--lg' : null,
    error ? 'mesh-field__control--invalid' : null,
    className,
  ]
    .filter((part): part is string => Boolean(part))
    .join(' ');

  return (
    <div className="mesh-field">
      <label className="mesh-field__label" htmlFor={inputId}>
        {label}
      </label>
      <input
        ref={ref}
        id={inputId}
        className={controlClasses}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        {...rest}
      />
      {hint ? (
        <p id={hintId} className="mesh-field__hint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="mesh-field__error">
          {error}
        </p>
      ) : null}
    </div>
  );
});
