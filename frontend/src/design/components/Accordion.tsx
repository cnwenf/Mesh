/**
 * 手风琴(design-quality.md §1.2 渐进披露):低频字段/技术细节折叠收纳。
 * - 头部为原生 <button>(Enter/Space 免费),aria-expanded + aria-controls;
 * - 面板 role=region + aria-labelledby;
 * - 单选(multiple=false)或允许多开;受控/非受控均可。
 * 无硬编码可见文案,全部来自 items。
 */
import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { Icon } from './Icon';
import './overlays.css';

export interface AccordionItem {
  value: string;
  title: string;
  content: ReactNode;
}

export interface AccordionProps {
  items: ReadonlyArray<AccordionItem>;
  /** 允许多个面板同时展开(默认单选) */
  multiple?: boolean;
  /** 受控展开集 */
  expanded?: ReadonlyArray<string>;
  /** 非受控初始展开集 */
  defaultExpanded?: ReadonlyArray<string>;
  onExpandedChange?: (expanded: ReadonlyArray<string>) => void;
  className?: string;
}

export function Accordion(props: AccordionProps): React.JSX.Element {
  const { items, multiple = false, expanded, defaultExpanded, onExpandedChange, className } = props;
  const baseId = useId();
  const [internalExpanded, setInternalExpanded] = useState<ReadonlyArray<string>>(
    () => defaultExpanded ?? [],
  );
  const current = expanded ?? internalExpanded;

  const toggle = (value: string): void => {
    const isOpen = current.includes(value);
    let next: ReadonlyArray<string>;
    if (multiple) {
      next = isOpen ? current.filter((entry) => entry !== value) : [...current, value];
    } else {
      next = isOpen ? [] : [value];
    }
    if (expanded === undefined) {
      setInternalExpanded(next);
    }
    onExpandedChange?.(next);
  };

  const rootClasses = ['mesh-accordion', className]
    .filter((part): part is string => Boolean(part))
    .join(' ');

  return (
    <div className={rootClasses}>
      {items.map((item) => {
        const isOpen = current.includes(item.value);
        const triggerId = `${baseId}-trigger-${item.value}`;
        const panelId = `${baseId}-panel-${item.value}`;
        return (
          <div key={item.value} className="mesh-accordion__item">
            <h3 className="mesh-accordion__heading">
              <button
                type="button"
                id={triggerId}
                className="mesh-accordion__trigger"
                aria-expanded={isOpen}
                aria-controls={panelId}
                onClick={() => toggle(item.value)}
              >
                {item.title}
                <span className="mesh-accordion__chevron" aria-hidden="true">
                  <Icon name="chevron-down" size={16} />
                </span>
              </button>
            </h3>
            {isOpen ? (
              <div
                id={panelId}
                role="region"
                aria-labelledby={triggerId}
                className="mesh-accordion__panel"
              >
                {item.content}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
