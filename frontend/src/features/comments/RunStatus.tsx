/**
 * AI 运行五态统一呈现(design-quality.md §9.8)。
 * 同一执行在评论占位等处使用相同文案、图标与 tone:
 *   queued(已排队)/ running(运行中)/ waiting(等待确认)/ succeeded(完成)/ failed(失败)。
 * 状态不只靠颜色:每态「图标形状 + 文案」双信号;running 附轻微脉冲动画(reduced-motion 关闭)。
 * failed 提供 onRetry 时显示重试入口(重试/介入)。纯展示 + 回调。
 */
import { Icon } from '../../design';
import type { IconName } from '../../design';
import { useT } from '../../i18n';
import './runStatus.css';

export type RunStatusKind = 'queued' | 'running' | 'waiting' | 'succeeded' | 'failed';

type RunStatusTone = 'neutral' | 'info' | 'warning' | 'success' | 'danger';

interface RunStatusConfig {
  readonly icon: IconName;
  readonly tone: RunStatusTone;
  readonly textKey: string;
}

/** 五态 → 图标/tone/文案键(全站统一,§9.8)。 */
export const RUN_STATUS_CONFIG: Readonly<Record<RunStatusKind, RunStatusConfig>> = Object.freeze({
  queued: { icon: 'clock', tone: 'neutral', textKey: 'comments.run.queued' },
  running: { icon: 'activity', tone: 'info', textKey: 'comments.run.running' },
  waiting: { icon: 'pause', tone: 'warning', textKey: 'comments.run.waiting' },
  succeeded: { icon: 'check', tone: 'success', textKey: 'comments.run.succeeded' },
  failed: { icon: 'error', tone: 'danger', textKey: 'comments.run.failed' },
});

export interface RunStatusProps {
  readonly status: RunStatusKind;
  /** 执行的 agent 名;提供时渲染「{name} · {状态文案}」。 */
  readonly agentName?: string | null;
  /** failed 态的重试/介入入口;缺省不渲染按钮。 */
  readonly onRetry?: () => void;
  /** 重试按钮文案(调用方提供,无默认)。 */
  readonly retryLabel?: string;
}

export function RunStatus(props: RunStatusProps): React.JSX.Element {
  const t = useT();
  const config = RUN_STATUS_CONFIG[props.status];
  const statusText = t(config.textKey);
  const hasAgent = props.agentName !== null && props.agentName !== undefined;
  const classes = [
    'mesh-run-status',
    `mesh-run-status--${config.tone}`,
    props.status === 'running' ? 'mesh-run-status--pulse' : null,
  ]
    .filter((part): part is string => part !== null)
    .join(' ');
  return (
    <span
      className={classes}
      data-testid={`run-status-${props.status}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <Icon name={config.icon} size={16} className="mesh-run-status__icon" />
      {/* agent 名为数据(非整句),与已本地化状态文案以分隔符并排,避免组件内拼接句子(§10.3)。 */}
      {hasAgent ? <span className="mesh-run-status__name">{props.agentName}</span> : null}
      <span className="mesh-run-status__text">{statusText}</span>
      {props.status === 'failed' && props.onRetry !== undefined ? (
        <button
          type="button"
          className="mesh-run-status__retry"
          data-testid="run-status-retry"
          onClick={props.onRetry}
        >
          {props.retryLabel ?? t('comments.run.retry')}
        </button>
      ) : null}
    </span>
  );
}
