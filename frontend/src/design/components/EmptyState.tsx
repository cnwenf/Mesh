/**
 * 空态(design-quality.md §7.7 四部分:插画/缘由/主操作/帮助链接)。
 * 与用户权限、上下文和下一步动作匹配(§3.1):主操作插槽由调用方按权限提供。
 * 全部文案/节点来自 prop,无硬编码可见字符串。
 */
import type { ReactNode } from 'react';
import './components.css';

export interface EmptyStateProps {
  title: string;
  description?: string;
  /** 主操作插槽(如「新建 issue」按钮) */
  action?: ReactNode;
  /** 插画插槽(统一风格的小型线性插画或图标,§7.7) */
  illustration?: ReactNode;
  /** 帮助链接或示例插槽(可选) */
  help?: ReactNode;
}

export function EmptyState(props: EmptyStateProps): React.JSX.Element {
  const { title, description, action, illustration, help } = props;
  return (
    <div className="mesh-empty-state">
      {illustration ? <div className="mesh-empty-state__illustration">{illustration}</div> : null}
      <p className="mesh-empty-state__title">{title}</p>
      {description ? <p className="mesh-empty-state__description">{description}</p> : null}
      {action ? <div className="mesh-empty-state__action">{action}</div> : null}
      {help ? <div className="mesh-empty-state__help">{help}</div> : null}
    </div>
  );
}
