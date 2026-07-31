/**
 * 页面模板 · DataView 批量操作条(design-quality.md §3.2:批量条粘底;§8.2 sticky
 * 计算安全区)。选中 ≥1 项时自底部滑入;承载选中计数、批量动作槽与取消选择。
 * z-index 使用 sticky 层级令牌;触控目标 ≥44px(§8.2)。
 */
import type { ReactNode } from 'react';
import './patterns.css';

export interface BulkBarProps {
  /** 当前选中数量;为 0 时整条不渲染 */
  readonly selectedCount: number;
  /** 计数文案(调用方按 locale 传入,如「已选 3 项」) */
  readonly countLabel: string;
  /** 取消选择回调与按钮文案 */
  readonly onClearSelection: () => void;
  readonly clearLabel: string;
  /** 批量动作槽(状态/优先级/删除等按钮) */
  readonly actions: ReactNode;
  /** 区域可访问名 */
  readonly ariaLabel: string;
}

export function BulkBar(props: BulkBarProps): React.JSX.Element | null {
  const { selectedCount, countLabel, onClearSelection, clearLabel, actions, ariaLabel } = props;
  if (selectedCount === 0) return null;
  return (
    <div className="mesh-bulk-bar" role="region" aria-label={ariaLabel} data-testid="bulk-bar">
      <span className="mesh-bulk-bar__count mesh-text-body-sm" aria-live="polite">
        {countLabel}
      </span>
      <div className="mesh-bulk-bar__actions">{actions}</div>
      <button
        type="button"
        className="mesh-bulk-bar__clear mesh-text-body-sm"
        onClick={onClearSelection}
      >
        {clearLabel}
      </button>
    </div>
  );
}
