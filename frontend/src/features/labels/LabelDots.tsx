import { useT } from '../../i18n';
import type { CompactLabel } from './types';
import './labels.css';

export interface LabelDotsProps {
  readonly labels: readonly CompactLabel[];
  readonly maxVisible?: number;
}

/** 卡片/行共用的紧凑标签摘要：数据色点 + 溢出计数。 */
export function LabelDots(props: LabelDotsProps): React.JSX.Element | null {
  const t = useT();
  const maxVisible = Math.max(0, props.maxVisible ?? 3);
  if (props.labels.length === 0) return null;
  const visible = props.labels.slice(0, maxVisible);
  const overflow = props.labels.length - visible.length;
  return (
    <span
      className="mesh-label-dots"
      data-testid="issue-label-summary"
      aria-label={t('labels.summaryAria', {
        names: props.labels.map((label) => label.name).join(', '),
      })}
    >
      {visible.map((label) => (
        <span
          key={label.id}
          className="mesh-label-dots__dot"
          data-testid="issue-label-dot"
          style={{ backgroundColor: label.color }}
          title={label.name}
          aria-hidden="true"
        />
      ))}
      {overflow > 0 ? (
        <span
          className="mesh-label-dots__overflow"
          data-testid="issue-label-overflow"
          aria-hidden="true"
        >
          +{overflow}
        </span>
      ) : null}
    </span>
  );
}
