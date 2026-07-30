/**
 * 环形上传进度(attachment.md design-quality §3.2「进度环」)。
 * 取代线性进度条:value 0–100 → SVG 圆弧(stroke-dashoffset);中心百分比(.mesh-tnum 表格数字)。
 * 颜色经 token:进度弧 var(--color-accent),轨道 var(--color-border-subtle);SVG 描边亦走 var()。
 * indeterminate(validating/completing 阶段)→ 旋转虚弧动画(reduced-motion 关闭)。
 * role=progressbar + aria-valuemin/max/now(不确定态省略 valuenow)+ 可访问名(label)。
 */
/* eslint-disable react-refresh/only-export-components -- clampPercent 纯函数与组件同文件共存(模块契约) */

/** 把百分比钳制到 0–100;非有限值回退 0。 */
export function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

export interface ProgressRingProps {
  /** 进度百分比 0–100(超界自动钳制)。 */
  readonly value: number;
  /** 直径(px),默认 44(兼作触控友好尺寸)。 */
  readonly size?: number;
  /** 描边宽度(px),默认 4。 */
  readonly strokeWidth?: number;
  /** 不确定态(validating/completing):旋转虚弧,不显示百分比。 */
  readonly indeterminate?: boolean;
  /** 可访问名(必填:进度环无可见文本标签时由 aria-label 承载语义)。 */
  readonly label: string;
}

export function ProgressRing(props: ProgressRingProps): React.JSX.Element {
  const size = props.size ?? 44;
  const strokeWidth = props.strokeWidth ?? 4;
  const indeterminate = props.indeterminate === true;
  const percent = clampPercent(props.value);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const center = size / 2;
  // 不确定态展示约 1/4 弧,经整体旋转形成「转动」动效。
  const dashOffset = indeterminate ? circumference * 0.75 : circumference * (1 - percent / 100);
  const rootClass = indeterminate
    ? 'mesh-progress-ring mesh-progress-ring--indeterminate'
    : 'mesh-progress-ring';
  return (
    <svg
      className={rootClass}
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="progressbar"
      aria-label={props.label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(percent)}
      data-testid="progress-ring"
    >
      <circle
        className="mesh-progress-ring__track"
        cx={center}
        cy={center}
        r={radius}
        fill="none"
        strokeWidth={strokeWidth}
      />
      <circle
        className="mesh-progress-ring__bar"
        cx={center}
        cy={center}
        r={radius}
        fill="none"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={dashOffset}
        transform={`rotate(-90 ${center} ${center})`}
      />
      {!indeterminate ? (
        <text
          className="mesh-progress-ring__text mesh-tnum"
          x={center}
          y={center}
          textAnchor="middle"
          dominantBaseline="central"
          data-testid="progress-ring-text"
        >
          {Math.round(percent)}%
        </text>
      ) : null}
    </svg>
  );
}
