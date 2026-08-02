/**
 * MobileMoreDrawer — 手机「更多」全高导航抽屉(design-quality §4.3)。
 *
 * 承载底部主导航之外的全部入口(按 §4.1 分组呈现:收件箱/项目/周期 | 成员/技能/
 * 小队 | 自动值守/运行环境/洞察 | 集成/设置,工作区设置按角色可见)。入口表与
 * 桌面侧栏同源(navigation.ts),保证桌面/手机导航语义一致、中文不重名。
 *
 * 浮层语义(焦点圈养/归还、Esc/遮罩关闭、dialog/aria-modal)复用 design 共享
 * Drawer 组件(§7.5;桌面侧出、0–599px 自动转底部 sheet)。无硬编码可见文案。
 */
import { NavLink } from 'react-router';
import { Drawer, Icon } from '../design';
import { useT } from '../i18n';
import { useOptionalWorkspace } from '../workspace/WorkspaceProvider';
import { NAV_GROUPS, MORE_DRAWER_KEYS, resolveNavTarget } from './navigation';
import type { NavGroupDef, NavItemDef } from './navigation';

export interface MobileMoreDrawerProps {
  open: boolean;
  onClose: () => void;
}

/** 抽屉分组:复用桌面分组定义,仅保留「更多」入口集(底部主导航四键除外) */
const DRAWER_GROUPS: ReadonlyArray<NavGroupDef> = NAV_GROUPS.map((group) => ({
  key: group.key,
  items: group.items.filter((item) => MORE_DRAWER_KEYS.includes(item.key)),
})).filter((group) => group.items.length > 0);

export function MobileMoreDrawer(props: MobileMoreDrawerProps): React.JSX.Element | null {
  const { open, onClose } = props;
  const t = useT();
  const workspaceContext = useOptionalWorkspace();
  const workspace = workspaceContext !== null ? workspaceContext.workspace : null;
  const showWorkspaceSettings =
    workspaceContext !== null && workspace !== null && workspaceContext.isAdmin;
  if (!open) {
    return null;
  }

  const renderItem = (item: NavItemDef): React.JSX.Element => (
    <li key={item.key} className="mesh-mobile-drawer__item">
      <NavLink
        to={resolveNavTarget(item, workspace?.slug ?? null)}
        data-testid={'mobile-drawer-nav-' + item.key}
        className={({ isActive }) =>
          isActive
            ? 'mesh-mobile-drawer__link mesh-mobile-drawer__link--active'
            : 'mesh-mobile-drawer__link'
        }
        onClick={onClose}
      >
        <Icon name={item.icon} size={20} className="mesh-mobile-drawer__icon" />
        <span>{t('nav.' + item.key)}</span>
      </NavLink>
    </li>
  );

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={t('mobileNav.moreTitle')}
      closeLabel={t('mobileNav.moreClose')}
    >
      <nav aria-label={t('mobileNav.moreTitle')}>
        {DRAWER_GROUPS.map((group) => (
          <section key={group.key} className="mesh-mobile-drawer__group">
            <h2 className="mesh-mobile-drawer__group-title">{t('nav.group.' + group.key)}</h2>
            <ul className="mesh-mobile-drawer__list">
              {group.items.map(renderItem)}
              {group.key === 'admin' && showWorkspaceSettings && workspace !== null ? (
                <li className="mesh-mobile-drawer__item">
                  <NavLink
                    to={`/w/${workspace.slug}/settings`}
                    data-testid="mobile-drawer-nav-workspace-settings"
                    className="mesh-mobile-drawer__link"
                    onClick={onClose}
                  >
                    <Icon name="settings" size={20} className="mesh-mobile-drawer__icon" />
                    <span>{t('nav.workspaceSettings')}</span>
                  </NavLink>
                </li>
              ) : null}
            </ul>
          </section>
        ))}
      </nav>
    </Drawer>
  );
}
