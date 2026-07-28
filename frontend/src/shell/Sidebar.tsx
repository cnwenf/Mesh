/**
 * 侧边导航(README §6.12 全局信息架构):首页/收件箱/项目/看板/成员/聊天/自动化/设置。
 * 全部为 NavLink(鼠标可达);快捷键仅为加速器(§6.12:所有快捷键有等价鼠标路径)。
 * 激活态经 className 回调应用,首页链接 `end` 精确匹配。
 */
import { NavLink, useLocation } from 'react-router';
import { useT } from '../i18n';
import { useOptionalWorkspace } from '../workspace/WorkspaceProvider';

export type NavKey =
  | 'home'
  | 'inbox'
  | 'projects'
  | 'issues'
  | 'board'
  | 'members'
  | 'skills'
  | 'squads'
  | 'cycles'
  | 'chat'
  | 'automation'
  | 'settings';

interface NavItem {
  readonly key: NavKey;
  readonly to: string;
}

const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { key: 'home', to: '/' },
  { key: 'inbox', to: '/inbox' },
  { key: 'projects', to: '/projects' },
  { key: 'issues', to: '/issues' },
  { key: 'board', to: '/board' },
  { key: 'members', to: '/members' },
  { key: 'skills', to: '/skills' },
  { key: 'squads', to: '/squads' },
  { key: 'cycles', to: '/cycles' },
  { key: 'chat', to: '/chat' },
  // 自动化入口指向 Runtimes 模块(runtime.md §4);nav.automation 文案保持不变。
  { key: 'automation', to: '/runtimes' },
  { key: 'settings', to: '/settings' },
];

function navLinkClassName({ isActive }: { isActive: boolean }): string {
  return isActive ? 'mesh-sidebar__link mesh-sidebar__link--active' : 'mesh-sidebar__link';
}

export function Sidebar(): React.JSX.Element {
  const t = useT();
  const location = useLocation();
  // 工作区上下文内 admin+ 呈现「工作区设置」入口(§6.12 角色可见性;member/guest 不可见)
  const workspaceContext = useOptionalWorkspace();
  const workspace = workspaceContext !== null ? workspaceContext.workspace : null;
  const showWorkspaceSettings =
    workspaceContext !== null && workspace !== null && workspaceContext.isAdmin;

  return (
    <nav className="mesh-sidebar" aria-label={t('a11y.sidebar')}>
      <ul className="mesh-sidebar__list">
        {NAV_ITEMS.map((item) => (
          <li key={item.key} className="mesh-sidebar__item">
            {/* 「看板」入口在选中视图路由 /views/{id} 下保持激活(§4.2 视图 URL 同步) */}
            <NavLink
              to={item.to}
              end={item.to === '/'}
              data-testid={'nav-' + item.key}
              className={({ isActive }) =>
                navLinkClassName({
                  isActive:
                    isActive || (item.key === 'board' && location.pathname.startsWith('/views/')),
                })
              }
            >
              {t('nav.' + item.key)}
            </NavLink>
          </li>
        ))}
        {showWorkspaceSettings && workspace !== null ? (
          <li className="mesh-sidebar__item">
            <NavLink
              to={`/w/${workspace.slug}/settings`}
              data-testid="nav-workspace-settings"
              className={navLinkClassName}
            >
              {t('nav.workspaceSettings')}
            </NavLink>
          </li>
        ) : null}
      </ul>
    </nav>
  );
}
