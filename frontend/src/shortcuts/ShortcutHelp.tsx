/**
 * 快捷键帮助层(? 打开,README §6.12):列出当前上下文(global + activeContexts)
 * 全部已注册快捷键,按分组呈现(分组标题经 groupLabels prop 提供);
 * 组合键经 Kbd + formatCombo 渲染,序列键拆为多个按键帽。构建于 Dialog 之上。
 */
import { Kbd } from '../design/components/Kbd';
import { Dialog } from '../design/components/Dialog';
import { useShortcutRegistry } from './registry';
import type { ShortcutContext } from './registry';
import { detectMac, formatCombo } from './ShortcutProvider';
import './shortcuts.css';

const GROUP_ORDER: ReadonlyArray<ShortcutContext> = ['global', 'board', 'issue', 'chat'];

export interface ShortcutHelpProps {
  open: boolean;
  onClose: () => void;
  /** 帮助层标题(dialog 可访问名) */
  title: string;
  /** 关闭按钮可访问名 */
  closeLabel: string;
  /** 分组标题文案(来自调用方,无硬编码) */
  groupLabels: Record<ShortcutContext, string>;
  /** 平台展示注入(Cmd/Ctrl),缺省 detectMac() */
  isMac?: boolean;
  /** 附加操作:恢复上手清单(onboarding.md §4.2 帮助菜单入口);两项同提供时才渲染 */
  restoreLabel?: string;
  onRestore?: () => void;
}

export function ShortcutHelp(props: ShortcutHelpProps): React.JSX.Element | null {
  const {
    open,
    onClose,
    title,
    closeLabel,
    groupLabels,
    isMac = detectMac(),
    restoreLabel,
    onRestore,
  } = props;
  const shortcuts = useShortcutRegistry((state) => state.shortcuts);
  const activeContexts = useShortcutRegistry((state) => state.activeContexts);

  if (!open) {
    return null;
  }

  const visibleGroups = GROUP_ORDER.filter(
    (group) => group === 'global' || activeContexts.includes(group),
  )
    .map((group) => ({
      group,
      defs: shortcuts.filter((def) => def.group === group),
    }))
    .filter((entry) => entry.defs.length > 0);

  return (
    <Dialog open={open} onClose={onClose} title={title} closeLabel={closeLabel}>
      <div className="mesh-shortcut-help">
        {visibleGroups.map(({ group, defs }) => (
          <section key={group} className="mesh-shortcut-help__group">
            <h3 className="mesh-shortcut-help__group-title">{groupLabels[group]}</h3>
            <ul className="mesh-shortcut-help__list">
              {defs.map((def) => (
                <li key={def.id} className="mesh-shortcut-help__item">
                  <span className="mesh-shortcut-help__label">{def.label}</span>
                  <span className="mesh-shortcut-help__combo">
                    {def.combo.split(' ').map((step, index) => (
                      <Kbd key={`${def.id}-${index}`}>{formatCombo(step, isMac)}</Kbd>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {restoreLabel !== undefined && onRestore !== undefined ? (
          <section className="mesh-shortcut-help__actions">
            <button
              type="button"
              className="mesh-shortcut-help__restore"
              data-testid="help-restore-onboarding"
              onClick={onRestore}
            >
              {restoreLabel}
            </button>
          </section>
        ) : null}
      </div>
    </Dialog>
  );
}
