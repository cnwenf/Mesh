/**
 * KPI 条(design-quality.md §3.2 / §8.3):响应式网格承载一组 <Kpi>。
 * 宽屏 auto-fit minmax 自适应;窄屏按 §8.3「KPI 两列或单列」:
 * ≤599px 两列、≤359px 单列(与 shell.css/analytics.css 既有断点一致)。
 */
import type { ReactNode } from 'react';

export interface KpiStripProps {
  readonly children: ReactNode;
  /** 可选可访问名(无标题的指标区给读屏一个名字) */
  readonly label?: string;
}

export function KpiStrip(props: KpiStripProps): React.JSX.Element {
  const { children, label } = props;
  return (
    <div
      className="mesh-analytics__kpi-strip"
      role={label !== undefined ? 'group' : undefined}
      aria-label={label}
    >
      {children}
    </div>
  );
}
