/**
 * 侧边导航(README §6.12 全局信息架构):首页/收件箱/项目/看板/成员/聊天/审批/
 * 自动化/设置。全部为 NavLink(鼠标可达);快捷键仅为加速器(§6.12:所有快捷键
 * 有等价鼠标路径)。激活态经 className 回调应用,首页链接 `end` 精确匹配。
 *
 * 链接形态(search-command-palette.md §3.4):工作区上下文内(/w/{slug}/*)一律
 * 生成规范深链 /w/{slug}/…;上下文外保持扁平路径(经 FlatRouteMigration 解析
 * active workspace 后 replace 至规范路由)。
 */
import { NavLink, useLocation, useMatch } from 'react-router';
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
  | 'approvals'
  | 'autopilots'
  | 'automation'
  | 'insights'
  | 'settings';

interface NavItem {
  readonly key: NavKey;
  /** 工作区上下文外的扁平后缀(上下文内前缀 /w/{slug}) */
  readonly suffix: string;
}

const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { key: 'home', suffix: '/' },
  { key: 'inbox', suffix: '/inbox' },
  { key: 'projects', suffix: '/projects' },
  { key: 'issues', suffix: '/issues' },
  { key: 'board', suffix: '/board' },
  { key: 'members', suffix: '/members' },
  { key: 'skills', suffix: '/automations/skills' },
  { key: 'squads', suffix: '/squads' },
  { key: 'cycles', suffix: '/cycles' },
  { key: 'chat', suffix: '/chat' },
  { key: 'approvals', suffix: '/approvals' },
  // 自动化规则(autopilot.md §4):AI 队友值班表;运行时(runtime.md)运营区入口。
  { key: 'autopilots', suffix: '/automations/autopilots' },
  { key: 'automation', suffix: '/automations/runtimes' },
  // 统计报表(analytics.md §4.1):工作区洞察仪表盘。
  { key: 'insights', suffix: '/insights' },
  { key: 'settings', suffix: '/settings' },
>>>>>>> 0b707ef3 (feat(frontend): 命令面板实体搜索 + 九条规范深链 + 快捷键四组上下文激活(MES-79))
];

function navLinkClassName({ isActive }: { isActive: boolean }): string {
  return isActive ? 'mesh-sidebar__link mesh-sidebar__link--active' : 'mesh-sidebar__link';
}

export function Sidebar(): React.JSX.Element {
  const t = useT();
  const location = useLocation();
  // URL 中的工作区 slug(Provider 加载期间亦可得,链接即刻呈规范形态)。
  const workspaceMatch = useMatch('/w/:workspaceSlug/*');
  const slug = workspaceMatch?.params.workspaceSlug;
  const prefix = slug !== undefined ? `/w/${slug}` : '';
  // 工作区上下文内 admin+ 呈现「工作区设置」入口(§6.12 角色可见性;member/guest 不可见)
  const workspaceContext = useOptionalWorkspace();
  const workspace = workspaceContext !== null ? workspaceContext.workspace : null;
  const showWorkspaceSettings =
    workspaceContext !== null && workspace !== null && workspaceContext.isAdmin;

  const toFor = (suffix: string): string =>
    suffix === '/' ? '/' : `${prefix}${suffix}`;

  return (
    <nav className="mesh-sidebar" aria-label={t('a11y.sidebar')}>
      <ul className="mesh-sidebar__list">
        {NAV_ITEMS.map((item) => (
          <li key={item.key} className="mesh-sidebar__item">
            {/* 「看板」入口在选中视图路由(/views/{id} 与 /w/{ws}/views/{id})下保持激活(§4.2) */}
            <NavLink
              to={toFor(item.suffix)}
              end={item.suffix === '/'}
              data-testid={'nav-' + item.key}
              className={({ isActive }) =>
                navLinkClassName({
                  isActive:
                    isActive ||
                    (item.key === 'board' &&
                      (location.pathname.startsWith('/views/') ||
                        location.pathname.includes('/views/'))),
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
