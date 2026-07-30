/**
 * 文字提示(design-quality.md §7.1/§9.1):
 * - hover 延迟出现(--motion-deliberate),focus-within 即时出现(键盘等价路径);
 * - role=tooltip + aria-describedby:单一元素子节点时关联到触发元素本身,
 *   其余情况挂在锚点容器上(焦点进入容器内控件时由读屏顺读);
 * - 纯 CSS 呈现,不承载唯一信息(仅增强);
 * - 图标按钮 MUST 配 tooltip + aria-label(§7.1)。
 */
import { cloneElement, isValidElement, useId } from 'react';
import type { ReactElement, ReactNode } from 'react';
import './primitives.css';

export interface TooltipProps {
  /** 提示文案(可见 + 读屏) */
  content: string;
  children: ReactNode;
  className?: string;
}

export function Tooltip(props: TooltipProps): React.JSX.Element {
  const { content, children, className } = props;
  const tooltipId = useId();
  const anchorClasses = ['mesh-tooltip-anchor', className]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  // 触发元素是单一组件时,直接把 aria-describedby 关联到它(读屏聚焦即读)
  const target = isValidElement(children)
    ? cloneElement(children as ReactElement<{ 'aria-describedby'?: string }>, {
        'aria-describedby': tooltipId,
      })
    : children;
  return (
    <span
      className={anchorClasses}
      aria-describedby={isValidElement(children) ? undefined : tooltipId}
    >
      {target}
      <span role="tooltip" id={tooltipId} className="mesh-tooltip">
        {content}
      </span>
    </span>
  );
}
