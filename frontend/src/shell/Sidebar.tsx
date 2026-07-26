/**
 * 侧边导航(README §6.12 全局信息架构):首页/收件箱/项目/看板/成员/聊天/自动化/设置。
 * 全部为 NavLink(鼠标可达);快捷键仅为加速器(§6.12:所有快捷键有等价鼠标路径)。
 * 激活态经 className 回调应用,首页链接 `end` 精确匹配。
 */
import { NavLink } from 'react-router-dom';
import { useT } from '../i18n';
import { useOptionalWorkspace } from '../workspace/WorkspaceProvider';

export type NavKey =
  | 'home'
  | 'inbox'
  | 'projects'
  | 'board'
  | 'members'
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
  { key: 'board', to: '/board' },
  { key: 'members', to: '/members' },
  { key: 'cycles', to: '/cycles' },
  { key: 'chat', to: '/chat' },
  { key: 'automation', to: '/automation' },
  { key: 'settings', to: '/settings' },
];

function navLinkClassName({ isActive }: { isActive: boolean }): string {
  return isActive ? 'mesh-sidebar__link mesh-sidebar__link--active' : 'mesh-sidebar__link';
}

export function Sidebar(): React.JSX.Element {
  const t = useT();
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
            <NavLink to={item.to} end={item.to === '/'} data-testid={'nav-' + item.key} className={navLinkClassName}>
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
