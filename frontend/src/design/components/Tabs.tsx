/**
 * 选项卡(design-quality.md §9.1:selected 与 hover 视觉不同 + ARIA 状态):
 * - role=tablist/tab/tabpanel;aria-selected、aria-controls、aria-labelledby;
 * - 漫游 tabindex:仅当前 tab 可 Tab 聚焦,←→/Home/End 切换焦点与选中;
 * - 受控(value+onChange)或非受控(defaultValue)。
 * 无硬编码可见文案,全部来自 items。
 */
import { useId, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';
import './overlays.css';

export interface TabItem {
  value: string;
  label: string;
  content: ReactNode;
  disabled?: boolean;
}

export interface TabsProps {
  items: ReadonlyArray<TabItem>;
  /** 受控当前值 */
  value?: string;
  /** 非受控初始值(缺省取首个可用项) */
  defaultValue?: string;
  onChange?: (value: string) => void;
  /** tablist 可访问名 */
  label: string;
  className?: string;
}

export function Tabs(props: TabsProps): React.JSX.Element {
  const { items, value, defaultValue, onChange, label, className } = props;
  const baseId = useId();
  const firstEnabled = items.find((item) => item.disabled !== true);
  const [internalValue, setInternalValue] = useState<string>(
    () => defaultValue ?? firstEnabled?.value ?? '',
  );
  const current = value ?? internalValue;
  // 兜底(验收 R1-M3):受控 value 未命中任何可用项(或命中禁用项)时,回退首个
  // 可用项为可聚焦/选中,杜绝整组 tabIndex=-1 使键盘永远进不去 tablist。
  const currentMatches = items.some((item) => item.value === current && item.disabled !== true);
  const effective = currentMatches ? current : (firstEnabled?.value ?? '');

  const select = (next: string): void => {
    if (value === undefined) {
      setInternalValue(next);
    }
    onChange?.(next);
  };

  const enabledItems = items.filter((item) => item.disabled !== true);

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    const enabledValues = enabledItems.map((item) => item.value);
    if (enabledValues.length === 0) return;
    const position = enabledValues.indexOf(effective);
    let next: string | null = null;
    if (event.key === 'ArrowRight') {
      next = enabledValues[(position + 1) % enabledValues.length];
    } else if (event.key === 'ArrowLeft') {
      next = enabledValues[(position - 1 + enabledValues.length) % enabledValues.length];
    } else if (event.key === 'Home') {
      next = enabledValues[0];
    } else if (event.key === 'End') {
      next = enabledValues[enabledValues.length - 1];
    }
    if (next !== null) {
      event.preventDefault();
      select(next);
      document.getElementById(`${baseId}-tab-${next}`)?.focus();
    }
  };

  const activeItem = items.find((item) => item.value === effective);
  const rootClasses = ['mesh-tabs', className]
    .filter((part): part is string => Boolean(part))
    .join(' ');

  return (
    <div className={rootClasses}>
      <div role="tablist" aria-label={label} className="mesh-tabs__list" onKeyDown={handleKeyDown}>
        {items.map((item) => {
          const selected = item.value === effective;
          return (
            <button
              key={item.value}
              type="button"
              role="tab"
              id={`${baseId}-tab-${item.value}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${item.value}`}
              tabIndex={selected ? 0 : -1}
              disabled={item.disabled === true}
              className="mesh-tabs__tab"
              onClick={() => select(item.value)}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      {activeItem !== undefined ? (
        <div
          role="tabpanel"
          id={`${baseId}-panel-${activeItem.value}`}
          aria-labelledby={`${baseId}-tab-${activeItem.value}`}
          className="mesh-tabs__panel"
          tabIndex={0}
        >
          {activeItem.content}
        </div>
      ) : null}
    </div>
  );
}
