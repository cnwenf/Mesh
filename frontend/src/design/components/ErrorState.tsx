/**
 * 错误态(异常态矩阵 retry 行):title + description + 可选重试按钮。
 * 重试按钮仅在 onRetry 与 retryLabel 同时提供时渲染(避免无标签控件)。无硬编码文案。
 */
import type { ReactNode } from 'react';
import { Button } from './Button';
import './components.css';

export interface ErrorStateProps {
  title: string;
  description?: string;
  /** 重试回调 */
  onRetry?: () => void;
  /** 重试按钮文案(来自调用方,配合 onRetry 使用) */
  retryLabel?: string;
  /** 插画插槽 */
  illustration?: ReactNode;
}

export function ErrorState(props: ErrorStateProps): React.JSX.Element {
  const { title, description, onRetry, retryLabel, illustration } = props;
  const showRetry = onRetry !== undefined && retryLabel !== undefined;
  return (
    <div className="mesh-error-state">
      {illustration ? <div className="mesh-error-state__illustration">{illustration}</div> : null}
      <p className="mesh-error-state__title">{title}</p>
      {description ? <p className="mesh-error-state__description">{description}</p> : null}
      {showRetry ? (
        <div className="mesh-error-state__action">
          <Button variant="secondary" onClick={onRetry}>
            {retryLabel}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
