/**
 * 骨架屏(异常态矩阵 loading 行):占位形状 aria-hidden;
 * 容器 role="status" + sr-only 加载文案(loadingLabel 必填,状态不止于动画/视觉)。
 */
import './components.css';
import { Skeleton as AppicaSkeleton } from '@appica/ui-react/skeleton';

export interface SkeletonProps {
  /** sr-only 加载文案(必填):读屏可感知的加载状态 */
  loadingLabel: string;
  /** 占位形状控制(宽高等由调用方经类名定义) */
  className?: string;
}

export function Skeleton(props: SkeletonProps): React.JSX.Element {
  const { loadingLabel, className } = props;
  const shapeClasses = ['mesh-skeleton__shape', className]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  return (
    <div className="mesh-skeleton" role="status">
      <AppicaSkeleton className={shapeClasses} effect="shimmer" />
      <span className="sr-only">{loadingLabel}</span>
    </div>
  );
}
