/**
 * Checkbox(design-quality §7.4/§9.1):原生 input[type=checkbox] + 自绘盒体
 * (accent 选中态 + 勾形 SVG),命中区 ≥44×44px 可达(视觉盒 18px,行级 label 承载
 * 点击);支持 indeterminate(半选,经 ref 同步原生属性);description/error 经
 * aria-describedby 关联;原生键盘(Space)与 focus-visible 环。无硬编码文案。
 */
import type { InputHTMLAttributes } from 'react';
import { forwardRef, useEffect, useId, useRef } from 'react';
import { useCallback } from 'react';
import { Icon } from './icons';
import './components.css';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /** 可见标签(必填) */
  label: string;
  /** 次级说明插槽(弱化文本,经 describedby 关联) */
  description?: string;
  /** 错误文案插槽 */
  error?: string;
  /** 半选态(批量选择父项;与 checked 的视觉组合由原生 indeterminate 表达) */
  indeterminate?: boolean;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, description, error, indeterminate = false, id, className, ...rest },
  ref,
) {
  const autoId = useId();
  const checkboxId = id ?? `mesh-checkbox-${autoId}`;
  const descriptionId = `${checkboxId}-description`;
  const errorId = `${checkboxId}-error`;
  const describedBy =
    [description ? descriptionId : null, error ? errorId : null]
      .filter((part): part is string => Boolean(part))
      .join(' ') || undefined;
  const innerRef = useRef<HTMLInputElement | null>(null);

  const setRefs = useCallback(
    (node: HTMLInputElement | null) => {
      innerRef.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref !== null && ref !== undefined) ref.current = node;
    },
    [ref],
  );

  useEffect(() => {
    if (innerRef.current !== null) innerRef.current.indeterminate = indeterminate;
  }, [indeterminate]);

  const rowClasses = ['mesh-checkbox', error ? 'mesh-checkbox--invalid' : null, className]
    .filter((part): part is string => Boolean(part))
    .join(' ');

  return (
    <div className={rowClasses}>
      <label className="mesh-checkbox__row" htmlFor={checkboxId}>
        <span className="mesh-checkbox__box-wrap">
          <input
            ref={setRefs}
            type="checkbox"
            id={checkboxId}
            className="mesh-checkbox__input"
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            {...rest}
          />
          <span className="mesh-checkbox__box" aria-hidden="true">
            {indeterminate ? (
              <span className="mesh-checkbox__minus" />
            ) : (
              <Icon name="check" size={16} className="mesh-checkbox__check" />
            )}
          </span>
        </span>
        <span className="mesh-checkbox__label">{label}</span>
      </label>
      {description ? (
        <p id={descriptionId} className="mesh-checkbox__description">
          {description}
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
