/**
 * 项目模块特性级小组件。徽章、头像和状态点统一经共享设计
 * 适配器渲染;项目状态始终保留文本信号，不只依赖颜色。
 */
import { Avatar, Badge, Button, StatusDot, Textarea } from '../../design';
import type { AvatarSize, BadgeTone } from '../../design';
import { useT } from '../../i18n';
import type { ProjectHealth, ProjectStatus } from './types';

export interface StatusBadgeProps {
  readonly status: ProjectStatus;
  /** 已本地化的状态文案(projects.status.<status>) */
  readonly label: string;
}

/** 项目状态徽章:planning=info / active=success / paused=warn / completed=neutral / cancelled=danger。 */
export function StatusBadge(props: StatusBadgeProps): React.JSX.Element {
  const tones: Readonly<Record<ProjectStatus, BadgeTone>> = {
    planning: 'info',
    active: 'success',
    paused: 'warning',
    completed: 'neutral',
    cancelled: 'danger',
  };
  return (
    <Badge
      tone={tones[props.status]}
      className={`mesh-projects__badge mesh-projects__badge--${props.status}`}
    >
      {props.label}
    </Badge>
  );
}

export interface HealthIndicatorProps {
  readonly health: ProjectHealth | null;
  /** 提供时整灯可点击(§4.2 点击健康度灯更新状态);渲染为 button */
  readonly onClick?: () => void;
  readonly updateLabel?: string;
  readonly testId?: string;
}

const HEALTH_TONES: Record<ProjectHealth, 'success' | 'warn' | 'danger'> = {
  on_track: 'success',
  at_risk: 'warn',
  off_track: 'danger',
};

/** 健康度灯(§4.2):三色圆点 + 必填文字标签;未设置时中性点 + 「未设置」文案。
 *  传 onClick 时整灯包成 button,点击打开「更新状态」(颜色从不作为唯一信号,label 仍在)。 */
export function HealthIndicator(props: HealthIndicatorProps): React.JSX.Element {
  const t = useT();
  const { health, onClick, updateLabel } = props;
  const dot =
    health === null ? (
      <StatusDot tone="neutral" label={t('projects.health.none')} />
    ) : (
      <StatusDot tone={HEALTH_TONES[health]} label={t(`projects.health.${health}`)} />
    );
  if (onClick === undefined) return dot;
  return (
    <Button
      variant="ghost"
      size="sm"
      className="mesh-projects__health-button"
      data-testid={props.testId ?? 'health-light-button'}
      onClick={onClick}
      aria-label={updateLabel ?? t('projects.detail.updateStatus')}
    >
      {dot}
    </Button>
  );
}

export interface AvatarInitialProps {
  /** 显示名;空名回退占位符 */
  readonly name: string;
  /** 可访问名(如负责人完整姓名) */
  readonly accessibleName?: string;
  readonly kind?: 'human' | 'agent';
  readonly size?: AvatarSize;
}

/** 项目负责人头像的兼容包装，内部使用共享头像语义。 */
export function AvatarInitial(props: AvatarInitialProps): React.JSX.Element {
  const displayName = props.accessibleName ?? (props.name.trim().length > 0 ? props.name : '?');
  return (
    <Avatar
      name={displayName}
      kind={props.kind ?? 'human'}
      size={props.size ?? 32}
      className="mesh-projects__avatar"
    />
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
  return (
    <Textarea
      label={props.label}
      className="mesh-projects__textarea"
      value={props.value}
      rows={props.rows ?? 3}
      placeholder={props.placeholder}
      onChange={(event) => props.onChange(event.target.value)}
    />
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
