/**
 * 桌面侧栏(design-quality §4.1):任务四分组(工作/团队/运行/管理)+ 可折叠 rail。
 *
 * - 展开 240px(--shell-sidebar-expanded):组标题 + 图标 + 文字;
 *   折叠 64px(--shell-sidebar-collapsed):仅图标,Tooltip 补齐可读名(§7.1
 *   图标按钮必须有 tooltip);折叠偏好持久化(localStorage,外壳偏好非业务状态)。
 * - 当前项:浅强调背景 + 强文字 + 3px 边缘指示(不以整块高饱和色作唯一信号,
 *   与手机底栏同构);aria-current=page 由 NavLink 自动表达。
 * - 入口表唯一真源见 navigation.ts;「看板」在 /views/{id} 下保持激活(§4.2)。
 * - 工作区设置入口按角色出现(admin+,member/guest 不可见,§6.12 角色可见性)。
 * - 链接形态(search-command-palette.md §3.4):工作区资源生成规范深链
 *   /w/{slug}/…(运行类入口归入 automations);账号设置保持全局 /settings。
 *   上下文外保持 navigation.ts 的静态路径,由 FlatRouteMigration 迁移。
 */
import { NavLink, useLocation } from 'react-router';
import { Icon, Tooltip } from '../design';
import { useT } from '../i18n';
import { useOptionalWorkspace } from '../workspace/WorkspaceProvider';
import { NAV_GROUPS, resolveNavTarget } from './navigation';
import type { NavItemDef } from './navigation';

export interface SidebarProps {
  /** 折叠为 64px rail(仅图标 + tooltip) */
  collapsed: boolean;
  /** 折叠/展开切换回调 */
  onToggleCollapsed: () => void;
}

function navLinkClassName({ isActive }: { isActive: boolean }): string {
  return isActive ? 'mesh-sidebar__link mesh-sidebar__link--active' : 'mesh-sidebar__link';
}

export function Sidebar(props: SidebarProps): React.JSX.Element {
  const { collapsed, onToggleCollapsed } = props;
  const t = useT();
  const location = useLocation();
  const workspaceContext = useOptionalWorkspace();
  const workspace = workspaceContext !== null ? workspaceContext.workspace : null;
  const showWorkspaceSettings =
    workspaceContext !== null && workspace !== null && workspaceContext.isAdmin;

  const renderItem = (item: NavItemDef, testKey: string): React.JSX.Element => {
    /* 「看板」入口在选中视图路由下保持激活(§4.2 视图 URL 同步;扁平/深链两形) */
    const isBoardView =
      item.key === 'board' &&
      (location.pathname.startsWith('/views/') ||
        (workspace !== null && location.pathname.startsWith(`/w/${workspace.slug}/views/`)));
    const link = (
      <NavLink
        to={resolveNavTarget(item, workspace?.slug ?? null)}
        title={t('nav.' + item.key)}
        end={item.end === true}
        data-testid={'nav-' + testKey}
        className={({ isActive }) => navLinkClassName({ isActive: isActive || isBoardView })}
      >
        <Icon name={item.icon} size={20} className="mesh-sidebar__link-icon" />
        <span className="mesh-sidebar__link-label">{t('nav.' + item.key)}</span>
      </NavLink>
    );
    // 折叠态:图标按钮语义经 Tooltip 补齐可读名(§7.1)
    return collapsed ? <Tooltip content={t('nav.' + item.key)}>{link}</Tooltip> : link;
  };

  return (
    <nav
      className={collapsed ? 'mesh-sidebar mesh-sidebar--collapsed' : 'mesh-sidebar'}
      aria-label={t('a11y.sidebar')}
    >
      {NAV_GROUPS.map((group) => (
        <section key={group.key} className="mesh-sidebar__group">
          {collapsed ? null : (
            <h2 className="mesh-sidebar__group-title">{t('nav.group.' + group.key)}</h2>
          )}
          <ul className="mesh-sidebar__list">
            {group.items.map((item) => (
              <li key={item.key} className="mesh-sidebar__item">
                {renderItem(item, item.key)}
              </li>
            ))}
            {group.key === 'admin' && showWorkspaceSettings && workspace !== null ? (
              <li className="mesh-sidebar__item">
                {collapsed ? (
                  <Tooltip content={t('nav.workspaceSettings')}>
                    <NavLink
                      to={`/w/${workspace.slug}/settings`}
                      title={t('nav.workspaceSettings')}
                      data-testid="nav-workspace-settings"
                      className={navLinkClassName}
                    >
                      <Icon name="settings" size={20} className="mesh-sidebar__link-icon" />
                      <span className="mesh-sidebar__link-label">{t('nav.workspaceSettings')}</span>
                    </NavLink>
                  </Tooltip>
                ) : (
                  <NavLink
                    to={`/w/${workspace.slug}/settings`}
                    title={t('nav.workspaceSettings')}
                    data-testid="nav-workspace-settings"
                    className={navLinkClassName}
                  >
                    <Icon name="settings" size={20} className="mesh-sidebar__link-icon" />
                    <span className="mesh-sidebar__link-label">{t('nav.workspaceSettings')}</span>
                  </NavLink>
                )}
              </li>
            ) : null}
          </ul>
        </section>
      ))}
      <div className="mesh-sidebar__footer">
        <Tooltip content={collapsed ? t('a11y.sidebarExpand') : t('a11y.sidebarCollapse')}>
          <button
            type="button"
            className="mesh-sidebar__toggle"
            data-testid="sidebar-toggle"
            aria-label={collapsed ? t('a11y.sidebarExpand') : t('a11y.sidebarCollapse')}
            aria-expanded={!collapsed}
            onClick={onToggleCollapsed}
          >
            <Icon name="panel-left" size={20} className="mesh-sidebar__toggle-icon" />
            <span className="mesh-sidebar__link-label">{t('a11y.sidebarCollapse')}</span>
          </button>
        </Tooltip>
      </div>
    </nav>
  );
}
