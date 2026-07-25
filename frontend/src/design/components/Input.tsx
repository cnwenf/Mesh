/**
 * 文本输入:`label` 渲染 <label htmlFor>;error/hint 经 aria-describedby 关联;
 * error 存在时 aria-invalid。受控友好(value/onChange 透传)。无硬编码文案。
 */
import type { InputHTMLAttributes } from 'react';
import { forwardRef, useId } from 'react';
import './components.css';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** 可见标签(必填):渲染 <label htmlFor> */
  label: string;
  /** 错误文案插槽 */
  error?: string;
  /** 提示文案插槽 */
  hint?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, error, hint, id, className, ...rest },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? `mesh-input-${autoId}`;
  const errorId = `${inputId}-error`;
  const hintId = `${inputId}-hint`;
  const describedBy =
    [error ? errorId : null, hint ? hintId : null].filter((part): part is string => Boolean(part)).join(' ') ||
    undefined;

  const controlClasses = ['mesh-field__control', error ? 'mesh-field__control--invalid' : null, className]
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
