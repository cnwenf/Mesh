/**
 * 下拉菜单(design-quality.md §7.5:低频行操作,不承载长表单):
 * - role=menu/menuitem;trigger aria-haspopup + aria-expanded;
 * - 键盘:Enter/Space/↓ 打开并聚焦首项,↑ 打开并聚焦末项;
 *   打开后 ↑↓ 漫游、Home/End 首末项、Esc 关闭并归还焦点、Tab 关闭;
 * - 外部 pointerdown 关闭;选择后关闭并归还焦点;
 * - danger 变体用于破坏性操作(仍需页面层确认流程,§7.3)。
 * 无硬编码可见文案,全部文案来自 items。
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';
import { Icon } from './Icon';
import type { IconName } from './Icon';
import './overlays.css';

export interface MenuItem {
  /** 唯一键 */
  key: string;
  /** 菜单项文案(来自调用方) */
  label: string;
  onSelect: () => void;
  icon?: IconName;
  danger?: boolean;
  disabled?: boolean;
}

/** 菜单条目:普通项或分隔线({ separator: true })。 */
export type MenuEntry = MenuItem | { readonly separator: true; readonly key: string };

function isSeparator(entry: MenuEntry): entry is { readonly separator: true; readonly key: string } {
  return 'separator' in entry && entry.separator;
}

export interface MenuProps {
  /** 触发按钮内容(图标或文案) */
  trigger: ReactNode;
  /** 触发按钮可访问名(图标触发时必填) */
  triggerLabel: string;
  entries: ReadonlyArray<MenuEntry>;
  /** 菜单靠右对齐(靠右操作列避免出界) */
  align?: 'start' | 'end';
  className?: string;
}

export function Menu(props: MenuProps): React.JSX.Element {
  const { trigger, triggerLabel, entries, align = 'start', className } = props;
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const items = entries.filter((entry): entry is MenuItem => !isSeparator(entry));

  const focusItem = useCallback((index: number): void => {
    const node = menuRef.current?.querySelector<HTMLElement>(`[data-menu-index="${index}"]`);
    node?.focus();
  }, []);

  const focusEdge = useCallback(
    (fromEnd: boolean): void => {
      if (items.length === 0) return;
      const enabledIndexes = items
        .map((item, index) => ({ item, index }))
        .filter(({ item }) => item.disabled !== true)
        .map(({ index }) => index);
      if (enabledIndexes.length === 0) return;
      focusItem(fromEnd ? enabledIndexes[enabledIndexes.length - 1] : enabledIndexes[0]);
    },
    [items, focusItem],
  );

  // 外部 pointerdown 关闭
  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent): void => {
      if (rootRef.current !== null && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [open]);

  const handleTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>): void => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      // 状态更新后聚焦首项
      requestAnimationFrame(() => focusEdge(false));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      requestAnimationFrame(() => focusEdge(true));
    }
  };

  const handleMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      setOpen(false);
      return;
    }
    if (event.key === 'Tab') {
      setOpen(false);
      return;
    }
    const enabledIndexes = items
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => item.disabled !== true)
      .map(({ index }) => index);
    if (enabledIndexes.length === 0) return;
    const active = document.activeElement;
    const currentIndex =
      active instanceof HTMLElement ? Number(active.dataset.menuIndex ?? -1) : -1;
    const position = enabledIndexes.indexOf(currentIndex);
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      const next = position < 0 ? enabledIndexes[0] : enabledIndexes[(position + 1) % enabledIndexes.length];
      focusItem(next);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      const previous =
        position <= 0 ? enabledIndexes[enabledIndexes.length - 1] : enabledIndexes[position - 1];
      focusItem(previous);
    } else if (event.key === 'Home') {
      event.preventDefault();
      focusItem(enabledIndexes[0]);
    } else if (event.key === 'End') {
      event.preventDefault();
      focusItem(enabledIndexes[enabledIndexes.length - 1]);
    }
  };

  const handleSelect = (item: MenuItem): void => {
    if (item.disabled === true) return;
    setOpen(false);
    item.onSelect();
  };

  const anchorClasses = ['mesh-menu-anchor', className]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  const menuClasses = ['mesh-menu', align === 'end' ? 'mesh-menu--end' : null]
    .filter((part): part is string => Boolean(part))
    .join(' ');

  let itemIndex = -1;
  return (
    <div className={anchorClasses} ref={rootRef}>
      <button
        type="button"
        className="mesh-button mesh-button--ghost mesh-icon-button mesh-icon-button--sm"
        aria-label={triggerLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={handleTriggerKeyDown}
      >
        <span className="mesh-icon-button__icon" aria-hidden="true">
          {trigger}
        </span>
      </button>
      {open ? (
        <div
          ref={menuRef}
          className={menuClasses}
          role="menu"
          id={menuId}
          tabIndex={-1}
          onKeyDown={handleMenuKeyDown}
        >
          {entries.map((entry) => {
            if (isSeparator(entry)) {
              return <div key={entry.key} className="mesh-menu__separator" role="separator" />;
            }
            itemIndex += 1;
            const item = entry;
            const index = itemIndex;
            const itemClasses = [
              'mesh-menu__item',
              item.danger === true ? 'mesh-menu__item--danger' : null,
            ]
              .filter((part): part is string => Boolean(part))
              .join(' ');
            return (
              <button
                key={item.key}
                type="button"
                role="menuitem"
                className={itemClasses}
                data-menu-index={index}
                tabIndex={-1}
                disabled={item.disabled === true}
                onClick={() => handleSelect(item)}
              >
                {item.icon !== undefined ? (
                  <span className="mesh-menu__item-icon" aria-hidden="true">
                    <Icon name={item.icon} size={16} />
                  </span>
                ) : null}
                {item.label}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
