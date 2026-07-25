/**
 * 命令面板(Ctrl/Cmd+K 打开,README §6.12)。
 *
 * - 命令来自 useShortcutRegistry;按 label + keywords 文本过滤;
 * - ArrowUp/Down 循环移动选择(aria-activedescendant + listbox/option),Enter 执行并关闭,
 *   Esc 关闭;点击选项同样执行(鼠标等价路径);
 * - 复用 Dialog 获得 role=dialog/aria-modal、焦点圈养与焦点归还;打开即聚焦搜索框;
 * - 全部文案经 prop(title/searchPlaceholder/emptyText/closeLabel),无硬编码可见字符串。
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { Dialog } from '../design/components/Dialog';
import { useShortcutRegistry } from './registry';
import type { ShortcutCommand } from './registry';
import './shortcuts.css';

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  /** 关闭按钮可访问名 */
  closeLabel: string;
  /** 搜索框占位符 */
  searchPlaceholder: string;
  /** 无匹配时的空态文案 */
  emptyText: string;
  /** 面板标题(dialog 可访问名) */
  title: string;
}

function optionId(commandId: string): string {
  return `mesh-palette-option-${commandId}`;
}

export function CommandPalette(props: CommandPaletteProps): React.JSX.Element | null {
  const { open, onClose, closeLabel, searchPlaceholder, emptyText, title } = props;
  const commands = useShortcutRegistry((state) => state.commands);
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (normalized.length === 0) {
      return commands;
    }
    return commands.filter(
      (command) =>
        command.label.toLowerCase().includes(normalized) ||
        (command.keywords ?? []).some((keyword) => keyword.toLowerCase().includes(normalized)),
    );
  }, [commands, query]);

  useEffect(() => {
    if (open) {
      setQuery('');
      inputRef.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  if (!open) {
    return null;
  }

  const runCommand = (command: ShortcutCommand): void => {
    command.run();
    onClose();
  };

  const handleInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>): void => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (filtered.length > 0) {
        setSelectedIndex((index) => (index + 1) % filtered.length);
      }
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (filtered.length > 0) {
        setSelectedIndex((index) => (index - 1 + filtered.length) % filtered.length);
      }
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const command = filtered[selectedIndex];
      if (command) {
        runCommand(command);
      }
    }
  };

  const selectedCommand = filtered[selectedIndex];

  return (
    <Dialog open={open} onClose={onClose} title={title} closeLabel={closeLabel}>
      <div className="mesh-palette">
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          className="mesh-palette__input"
          placeholder={searchPlaceholder}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleInputKeyDown}
          aria-expanded={filtered.length > 0}
          aria-controls={listId}
          aria-activedescendant={selectedCommand ? optionId(selectedCommand.id) : undefined}
          autoComplete="off"
        />
        {filtered.length === 0 ? (
          <p className="mesh-palette__empty">{emptyText}</p>
        ) : (
          <ul id={listId} role="listbox" className="mesh-palette__list" aria-label={title}>
            {filtered.map((command, index) => (
              <li
                key={command.id}
                id={optionId(command.id)}
                role="option"
                aria-selected={index === selectedIndex}
                className={
                  index === selectedIndex
                    ? 'mesh-palette__option mesh-palette__option--active'
                    : 'mesh-palette__option'
                }
                onMouseEnter={() => setSelectedIndex(index)}
                onClick={() => runCommand(command)}
              >
                {command.label}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Dialog>
  );
}
