/**
 * 状态点(连接/运行状态指示)。
 * §6.12 硬约束:脉冲动画/颜色不得作为唯一状态信号 —— 色点一律 aria-hidden,
 * 状态必须经 label 文本表达(必填);pulse 仅为可选的叠加视觉提示。
 */
import './components.css';

export type StatusDotTone = 'success' | 'warn' | 'danger' | 'info' | 'neutral';

export interface StatusDotProps {
  tone: StatusDotTone;
  /** 状态文本(必填):颜色/脉冲之外的权威信号 */
  label: string;
  /** 可选叠加脉冲动画(文本信号始终存在) */
  pulse?: boolean;
}

export function StatusDot(props: StatusDotProps): React.JSX.Element {
  const { tone, label, pulse = false } = props;
  const dotClasses = ['mesh-status__dot', `mesh-status__dot--${tone}`, pulse ? 'mesh-status__dot--pulse' : null]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  return (
    <span className="mesh-status">
      <span className={dotClasses} aria-hidden="true" />
      <span className="mesh-status__label">{label}</span>
    </span>
  );
}
