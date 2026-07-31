/**
 * 页面模板 · DataView 过滤 chips(design-quality.md §3.2 Issue 列表行:过滤 chips)。
 *
 * 每个 chip = 一个生效过滤条件:标签 + 值 + 移除按钮(非颜色唯一信号:带 close
 * 图标与可访问名)。「清除全部」仅在 chips ≥2 时出现,避免单项噪音。
 *
 * 设计层与 i18n 解耦:一切可见文案经 props 传入(与 Menu/Drawer 一致)。
 */
import { Icon } from '../components/Icon';
import './patterns.css';

export interface FilterChip {
  readonly key: string;
  /** 条件字段名(如「优先级」) */
  readonly label: string;
  /** 条件值(如「高」),省略时只渲染 label */
  readonly value?: string;
  /** 移除按钮可访问名(调用方按 locale 传入,如「移除过滤:优先级」) */
  readonly removeLabel: string;
  readonly onRemove: () => void;
}

export interface FilterChipsProps {
  readonly chips: readonly FilterChip[];
  /** 区域可访问名(调用方按 locale 传入) */
  readonly ariaLabel: string;
  /** 提供且 chips ≥2 时渲染清除全部按钮 */
  readonly onClearAll?: () => void;
  readonly clearAllLabel?: string;
}

export function FilterChips(props: FilterChipsProps): React.JSX.Element | null {
  const { chips, ariaLabel, onClearAll, clearAllLabel } = props;
  if (chips.length === 0) return null;
  return (
    <div className="mesh-filter-chips" role="region" aria-label={ariaLabel}>
      <ul className="mesh-filter-chips__list">
        {chips.map((chip) => (
          <li key={chip.key} className="mesh-filter-chips__item" data-testid={`filter-chip-${chip.key}`}>
            <span className="mesh-filter-chips__label">{chip.label}</span>
            {chip.value !== undefined && chip.value !== '' ? (
              <span className="mesh-filter-chips__value">{chip.value}</span>
            ) : null}
            <button
              type="button"
              className="mesh-filter-chips__remove"
              aria-label={chip.removeLabel}
              onClick={chip.onRemove}
            >
              <Icon name="close" size={16} />
            </button>
          </li>
        ))}
      </ul>
      {onClearAll !== undefined && clearAllLabel !== undefined && chips.length >= 2 ? (
        <button type="button" className="mesh-filter-chips__clear" onClick={onClearAll}>
          {clearAllLabel}
        </button>
      ) : null}
    </div>
  );
}
