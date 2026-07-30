/**
 * 下拉选择:`label` 渲染 <label htmlFor>;error 经 aria-describedby 关联 + aria-invalid;
 * children 为 <option> 列表。无硬编码文案。
 */
import type { ReactNode, SelectHTMLAttributes } from 'react';
import { forwardRef, useId } from 'react';
import './components.css';

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  /** 可见标签(必填):渲染 <label htmlFor> */
  label: string;
  /** 错误文案插槽 */
  error?: string;
  /** <option> 列表 */
  children: ReactNode;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, error, id, className, children, ...rest },
  ref,
) {
  const autoId = useId();
  const selectId = id ?? `mesh-select-${autoId}`;
  const errorId = `${selectId}-error`;

  const controlClasses = ['mesh-field__control', error ? 'mesh-field__control--invalid' : null, className]
    .filter((part): part is string => Boolean(part))
    .join(' ');

  return (
    <div className="mesh-field">
      <label className="mesh-field__label" htmlFor={selectId}>
        {label}
      </label>
      <select
        ref={ref}
        id={selectId}
        className={controlClasses}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        {...rest}
      >
        {children}
      </select>
      {error ? (
        <p id={errorId} className="mesh-field__error">
          {error}
        </p>
      ) : null}
    </div>
  );
});
