/**
 * MobileMoreDrawer — 手机「更多」全高导航抽屉(design-quality §4.3)。
 *
 * 承载底部主导航之外的全部入口(收件箱/项目/成员/技能/小队/周期/自动值守/
 * 运行环境/洞察/集成/设置,工作区设置按角色可见)。与 Sidebar 同一 nav.* 文案源、
 * 同一路由表,保证桌面/手机导航语义一致。
 *
 * 焦点圈养与归还、Esc/遮罩关闭移植自 design/Dialog 的成熟模式
 * (Phase 1 将合并到 design 共享 Drawer 组件,见 MES-111 实施计划 Task 17)。
 * 无硬编码可见文案。
 */
import { useEffect, useId, useRef } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { NavLink } from 'react-router';
import { IconButton } from '../design';
import { useT } from '../i18n';
import { useOptionalWorkspace } from '../workspace/WorkspaceProvider';

export interface MobileMoreDrawerProps {
  open: boolean;
  onClose: () => void;
}

interface DrawerNavItem {
  readonly key: string;
  readonly to: string;
}

const DRAWER_NAV_ITEMS: ReadonlyArray<DrawerNavItem> = [
  { key: 'inbox', to: '/inbox' },
  { key: 'projects', to: '/projects' },
  { key: 'members', to: '/members' },
  { key: 'skills', to: '/skills' },
  { key: 'squads', to: '/squads' },
  { key: 'cycles', to: '/cycles' },
  // 与 Sidebar 同源:自动值守(autopilot.md)/ 运行环境(runtime.md),中文不再重名。
  { key: 'autopilots', to: '/autopilots' },
  { key: 'automation', to: '/runtimes' },
  { key: 'insights', to: '/insights' },
  { key: 'integrations', to: '/integrations' },
  { key: 'settings', to: '/settings' },
];

// 与 design/components/Dialog.tsx 同源(焦点圈养);Phase 1 合并到共享 Drawer。
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

export function MobileMoreDrawer(props: MobileMoreDrawerProps): React.JSX.Element | null {
  const { open, onClose } = props;
  const t = useT();
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);
  const workspaceContext = useOptionalWorkspace();
  const workspace = workspaceContext !== null ? workspaceContext.workspace : null;
  const showWorkspaceSettings =
    workspaceContext !== null && workspace !== null && workspaceContext.isAdmin;

  useEffect(() => {
    if (!open) return;
    previouslyFocusedRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panelRef.current?.focus();
    return () => {
      previouslyFocusedRef.current?.focus();
    };
  }, [open]);

  if (!open) {
    return null;
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const root = panelRef.current;
    if (!root) return;
    const focusables = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
    if (focusables.length === 0) {
      event.preventDefault();
      root.focus();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === root)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="mesh-mobile-drawer">
      <div className="mesh-mobile-drawer__scrim" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        className="mesh-mobile-drawer__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <div className="mesh-mobile-drawer__header">
          <h2 id={titleId} className="mesh-mobile-drawer__title">
            {t('mobileNav.moreTitle')}
          </h2>
          <IconButton
            label={t('mobileNav.moreClose')}
            data-testid="mobile-drawer-close"
            onClick={onClose}
          >
            ×
          </IconButton>
        </div>
        <nav aria-label={t('mobileNav.moreTitle')}>
          <ul className="mesh-mobile-drawer__list">
            {DRAWER_NAV_ITEMS.map((item) => (
              <li key={item.key} className="mesh-mobile-drawer__item">
                <NavLink
                  to={item.to}
                  data-testid={'mobile-drawer-nav-' + item.key}
                  className={({ isActive }) =>
                    isActive
                      ? 'mesh-mobile-drawer__link mesh-mobile-drawer__link--active'
                      : 'mesh-mobile-drawer__link'
                  }
                  onClick={onClose}
                >
                  {t('nav.' + item.key)}
                </NavLink>
              </li>
            ))}
            {showWorkspaceSettings && workspace !== null ? (
              <li className="mesh-mobile-drawer__item">
                <NavLink
                  to={`/w/${workspace.slug}/settings`}
                  data-testid="mobile-drawer-nav-workspace-settings"
                  className="mesh-mobile-drawer__link"
                  onClick={onClose}
                >
                  {t('nav.workspaceSettings')}
                </NavLink>
              </li>
            ) : null}
          </ul>
        </nav>
      </div>
    </div>
  );
}
