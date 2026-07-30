/**
 * Toolbar(design-quality §4.4 DataView/Board 工具条):视图/筛选/批量操作容器。
 * role=toolbar + aria-label;窄屏自动换行(sticky 与否由调用方页面决定,sticky 时
 * 页面须计算顶栏/安全区高度,§8.2)。无硬编码文案。
 */
import type { ReactNode } from 'react';
import './components.css';

export interface ToolbarProps {
  /** 工具条可访问名(必填:说明这组控件的作用) */
  label: string;
  children: ReactNode;
  className?: string;
}

export function Toolbar(props: ToolbarProps): React.JSX.Element {
  const { label, children, className } = props;
  const classes = ['mesh-toolbar', className].filter((part): part is string => Boolean(part)).join(' ');

  return (
    <div role="toolbar" aria-label={label} className={classes}>
      {children}
    </div>
  );
}
