/**
 * RunStateBadge — AI 运行反馈五态统一徽标(design-quality.md §9.8 统一语言)。
 *
 * 「同一执行在 issue、评论占位、收件箱、agent 详情和执行页使用相同文案、图标和 tone」:
 * 本组件是图标 + tone 的单一事实源;文案经调用方 `t('runState.<state>')` 统一提供
 * (design 层不依赖 i18n,标签一律 prop 传入,与 Badge/StatusDot 一致)。
 *
 * 五态(§9.8)+ 两个派生态:
 * - queued    已排队        tone=info
 * - running   运行中        tone=accent(附加脉冲,经 prefers-reduced-motion 降级)
 * - waiting   等待人工确认  tone=warning
 * - succeeded 完成          tone=success
 * - failed    失败          tone=danger
 * - idle      空闲(无在途执行,聚合 presence 全 0) tone=neutral,无图标
 * - unknown   未知(presence 帧未至) tone=neutral
 *
 * 颜色不是唯一信号:tone 默认图标 + 文案双通道(§7.2);`data-state` 供测试与样式钩子。
 */
import { Badge } from '../components/Badge';
import type { BadgeSize, BadgeTone } from '../components/Badge';
import type { IconName } from '../components/Icon';
import './RunStateBadge.css';

export type RunState =
  | 'queued'
  | 'running'
  | 'waiting'
  | 'succeeded'
  | 'failed'
  | 'idle'
  | 'unknown';

/** §9.8 统一 tone:状态语义到状态三元组的固定映射(禁调用方各自着色)。 */
export const RUN_STATE_TONES: Readonly<Record<RunState, BadgeTone>> = Object.freeze({
  queued: 'info',
  running: 'accent',
  waiting: 'warning',
  succeeded: 'success',
  failed: 'danger',
  idle: 'neutral',
  unknown: 'neutral',
});

/** §9.8 统一图标:null = 经 tone 默认图标(BADGE_TONE_ICONS);非 null = 显式覆盖。 */
export const RUN_STATE_ICONS: Readonly<Record<RunState, IconName | null>> = Object.freeze({
  queued: null,
  running: null,
  waiting: null,
  succeeded: null,
  failed: null,
  idle: null,
  unknown: 'info',
});

export interface RunStateBadgeProps {
  /** 运行态(§9.8 五态 + idle/unknown 派生态) */
  state: RunState;
  /** 可见文案(调用方经 t(`runState.${state}`) 提供,统一语言) */
  label: string;
  size?: BadgeSize;
  className?: string;
}

export function RunStateBadge(props: RunStateBadgeProps): React.JSX.Element {
  const { state, label, size, className } = props;
  const classes = ['mesh-run-state-badge', `mesh-run-state-badge--${state}`, className]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  return (
    <span className={classes} data-state={state}>
      <Badge tone={RUN_STATE_TONES[state]} icon={RUN_STATE_ICONS[state] ?? undefined} size={size}>
        {label}
      </Badge>
    </span>
  );
}
