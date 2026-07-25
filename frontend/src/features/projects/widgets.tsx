/**
 * 项目模块特性级小组件(设计系统无 Badge/Avatar/ProgressBar,按 §6.12 就地实现)。
 * 仅用语义 token 着色(见 projects.css);状态语义一律有文本兜底,颜色非唯一信号。
 */
import { useId } from 'react';
import { StatusDot } from '../../design';
import { useT } from '../../i18n';
import type { ProjectHealth, ProjectStatus } from './types';

export interface StatusBadgeProps {
  readonly status: ProjectStatus;
  /** 已本地化的状态文案(projects.status.<status>) */
  readonly label: string;
}

/** 项目状态徽章:planning=info / active=success / paused=warn / completed=neutral / cancelled=danger。 */
export function StatusBadge(props: StatusBadgeProps): React.JSX.Element {
  return (
    <span className={`mesh-projects__badge mesh-projects__badge--${props.status}`}>
      {props.label}
    </span>
  );
}

export interface HealthIndicatorProps {
  readonly health: ProjectHealth | null;
}

const HEALTH_TONES: Record<ProjectHealth, 'success' | 'warn' | 'danger'> = {
  on_track: 'success',
  at_risk: 'warn',
  off_track: 'danger',
};

/** 健康度灯(§4.2):三色圆点 + 必填文字标签;未设置时中性点 + 「未设置」文案。 */
export function HealthIndicator(props: HealthIndicatorProps): React.JSX.Element {
  const t = useT();
  const { health } = props;
  if (health === null) {
    return <StatusDot tone="neutral" label={t('projects.health.none')} />;
  }
  return <StatusDot tone={HEALTH_TONES[health]} label={t(`projects.health.${health}`)} />;
}

export interface AvatarInitialProps {
  /** 显示名(取首字符大写);空名回退占位符 */
  readonly name: string;
  /** 可访问名(如负责人姓名);提供时渲染 sr-only 文本 */
  readonly accessibleName?: string;
}

/** 头像首字圆(人/agent 通用;§4.1 卡片负责人位)。 */
export function AvatarInitial(props: AvatarInitialProps): React.JSX.Element {
  const initial = props.name.length > 0 ? props.name.slice(0, 1).toUpperCase() : '?';
  return (
    <span className="mesh-projects__avatar" aria-hidden={props.accessibleName === undefined}>
      {initial}
      {props.accessibleName !== undefined ? (
        <span className="sr-only">{props.accessibleName}</span>
      ) : null}
    </span>
  );
}

export interface LabeledTextareaProps {
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly placeholder?: string;
  readonly rows?: number;
}

/** 带可见标签的文本域(设计系统无 Textarea,复用 .mesh-field 词汇)。 */
export function LabeledTextarea(props: LabeledTextareaProps): React.JSX.Element {
  const autoId = useId();
  const textareaId = `mesh-projects-textarea-${autoId}`;
  return (
    <div className="mesh-field">
      <label className="mesh-field__label" htmlFor={textareaId}>
        {props.label}
      </label>
      <textarea
        id={textareaId}
        className="mesh-field__control mesh-projects__textarea"
        value={props.value}
        rows={props.rows ?? 3}
        placeholder={props.placeholder}
        onChange={(event) => props.onChange(event.target.value)}
      />
    </div>
  );
}

export interface ProgressBarProps {
  /** 0..1 */
  readonly progress: number;
  /** 悬停/可访问提示(如 `3/10 done`) */
  readonly title: string;
}

/** 条形进度(§4.2):宽度由 progress 派生;title 暴露 done/total 数值信号。 */
export function ProgressBar(props: ProgressBarProps): React.JSX.Element {
  const clamped = Math.min(1, Math.max(0, props.progress));
  const percent = Math.round(clamped * 100);
  return (
    <div
      className="mesh-projects__progress"
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={props.title}
      title={props.title}
    >
      <span className="mesh-projects__progress-fill" style={{ inlineSize: `${percent}%` }} />
    </div>
  );
}
