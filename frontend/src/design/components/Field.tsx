/**
 * Field(design-quality §7.4):label/control/hint/error 一体化字段外壳。
 *
 * Input/Select 内嵌同款结构;Field 面向其余控件(Textarea/Checkbox 组/自定义控件)
 * 提供同一视觉与无障碍契约:
 * - 经 render-prop 下发 controlProps(id / aria-invalid / aria-describedby / aria-required),
 *   控件与错误/提示的关联由外壳保证,调用方只展开即可,杜绝漏关联;
 * - error 存在即 aria-invalid;hint 与 error 可同时存在(describedby 合并);
 * - 无硬编码文案。
 */
import type { ReactNode } from 'react';
import { useId } from 'react';
import './components.css';

export interface FieldControlProps {
  readonly id: string;
  readonly 'aria-invalid': true | undefined;
  readonly 'aria-describedby': string | undefined;
  readonly 'aria-required': true | undefined;
}

export interface FieldRenderArgument {
  /** 展开到控件上的无障碍属性(关联 label/hint/error) */
  readonly controlProps: FieldControlProps;
  readonly errorId: string;
  readonly hintId: string;
}

export interface FieldProps {
  /** 可见标签(必填):渲染 <label htmlFor> */
  label: string;
  /** 控件 id 前缀(缺省自动生成) */
  id?: string;
  /** 错误文案插槽 */
  error?: string;
  /** 提示文案插槽 */
  hint?: string;
  /** 必填标记(仅影响 aria-required 与视觉星号,校验由调用方掌握) */
  required?: boolean;
  /** 控件渲染槽:接收 controlProps 展开到控件 */
  children: (argument: FieldRenderArgument) => ReactNode;
}

export function Field(props: FieldProps): React.JSX.Element {
  const { label, id, error, hint, required = false, children } = props;
  const autoId = useId();
  const controlId = id ?? `mesh-field-${autoId}`;
  const errorId = `${controlId}-error`;
  const hintId = `${controlId}-hint`;
  const describedBy =
    [error ? errorId : null, hint ? hintId : null].filter((part): part is string => Boolean(part)).join(' ') ||
    undefined;

  const controlProps: FieldControlProps = {
    id: controlId,
    'aria-invalid': error ? true : undefined,
    'aria-describedby': describedBy,
    'aria-required': required ? true : undefined,
  };

  return (
    <div className="mesh-field">
      <label className="mesh-field__label" htmlFor={controlId}>
        {label}
        {required ? (
          <span className="mesh-field__required" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      {children({ controlProps, errorId, hintId })}
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
}
