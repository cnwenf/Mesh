/**
 * 多行文本(design-quality §7.4):与 Input 同构的字段外壳(label/hint/error +
 * aria-describedby);高度自适应但有最大高度(超出内部滚动),rows 定初始高;
 * 字号经 --font-size-control(0–599px 为 16px,防 iOS 缩放)。无硬编码文案。
 */
import type { TextareaHTMLAttributes } from 'react';
import { forwardRef, useCallback, useEffect, useId, useRef } from 'react';
import { Textarea as AppicaTextarea } from '@appica/ui-react/textarea';
import './components.css';

/** 自适应上限(§7.4:textarea 自适应但有最大高度) */
export const TEXTAREA_MAX_HEIGHT_PX = 320;

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** 可见标签(必填):渲染 <label htmlFor> */
  label: string;
  /** 错误文案插槽 */
  error?: string;
  /** 提示文案插槽 */
  hint?: string;
  /** 自适应高度上限(px),默认 TEXTAREA_MAX_HEIGHT_PX */
  maxHeight?: number;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, error, hint, maxHeight = TEXTAREA_MAX_HEIGHT_PX, id, className, rows = 3, onChange, ...rest },
  ref,
) {
  const autoId = useId();
  const textareaId = id ?? `mesh-textarea-${autoId}`;
  const errorId = `${textareaId}-error`;
  const hintId = `${textareaId}-hint`;
  const describedBy =
    [error ? errorId : null, hint ? hintId : null].filter((part): part is string => Boolean(part)).join(' ') ||
    undefined;
  const innerRef = useRef<HTMLTextAreaElement | null>(null);

  const setRefs = useCallback(
    (node: HTMLTextAreaElement | null) => {
      innerRef.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref !== null && ref !== undefined) ref.current = node;
    },
    [ref],
  );

  const resize = useCallback(() => {
    const node = innerRef.current;
    if (node === null) return;
    node.style.blockSize = 'auto';
    const next = Math.min(node.scrollHeight, maxHeight);
    node.style.blockSize = `${next}px`;
    node.style.overflowY = node.scrollHeight > maxHeight ? 'auto' : 'hidden';
  }, [maxHeight]);

  useEffect(() => {
    resize();
  }, [resize, rows]);

  const controlClasses = [
    'mesh-field__control',
    'mesh-textarea',
    error ? 'mesh-field__control--invalid' : null,
    className,
  ]
    .filter((part): part is string => Boolean(part))
    .join(' ');

  return (
    <div className="mesh-field">
      <label className="mesh-field__label" htmlFor={textareaId}>
        {label}
      </label>
      <AppicaTextarea
        ref={setRefs}
        id={textareaId}
        rows={rows}
        className={controlClasses}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        onChange={(event) => {
          onChange?.(event);
          resize();
        }}
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
