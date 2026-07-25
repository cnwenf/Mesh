/**
 * 空态(异常态矩阵 empty 行):title + description + 主操作插槽 + 插画插槽。
 * 全部文案/节点来自 prop,无硬编码可见字符串。
 */
import type { ReactNode } from 'react';
import './components.css';

export interface EmptyStateProps {
  title: string;
  description?: string;
  /** 主操作插槽(如「新建 issue」按钮) */
  action?: ReactNode;
  /** 插画插槽(空态插画) */
  illustration?: ReactNode;
}

export function EmptyState(props: EmptyStateProps): React.JSX.Element {
  const { title, description, action, illustration } = props;
  return (
    <div className="mesh-empty-state">
      {illustration ? <div className="mesh-empty-state__illustration">{illustration}</div> : null}
      <p className="mesh-empty-state__title">{title}</p>
      {description ? <p className="mesh-empty-state__description">{description}</p> : null}
      {action ? <div className="mesh-empty-state__action">{action}</div> : null}
    </div>
  );
}
