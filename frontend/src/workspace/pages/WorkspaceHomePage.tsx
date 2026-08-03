/**
 * 工作区首页(/w/:workspaceSlug)—— 当前工作区概览(workspace.md §4.1)。
 *
 * 呈现工作区名称/slug/我的角色/默认 locale;admin+ 提供设置入口(§6.12 角色可见性)。
 */
import { Link } from 'react-router';
import { Badge, Icon, PageHeader, buttonClasses } from '../../design';
import type { IconName } from '../../design';
import { useT } from '../../i18n';
import { useWorkspace, WorkspaceGate } from '../WorkspaceProvider';
import './WorkspaceHomePage.css';

export function WorkspaceHomePage(): React.JSX.Element {
  return (
    <WorkspaceGate>
      <WorkspaceOverview />
    </WorkspaceGate>
  );
}

function WorkspaceOverview(): React.JSX.Element {
  const { workspace: gatedWorkspace, isAdmin } = useWorkspace();
  const t = useT();
  // WorkspaceGate 只在当前工作区已解析时渲染子树。
  const workspace = gatedWorkspace!;
  const defaultLocale =
    typeof workspace.settings.default_locale === 'string'
      ? workspace.settings.default_locale
      : 'en';
  const roleLabel = t(`roles.${workspace.my_role}`);
  const quickLinks: ReadonlyArray<{ label: string; path: string; icon: IconName; testId: string }> =
    [
      {
        label: t('nav.projects'),
        path: `/w/${workspace.slug}/projects`,
        icon: 'folder',
        testId: 'ws-quick-projects',
      },
      {
        label: t('nav.issues'),
        path: `/w/${workspace.slug}/issues`,
        icon: 'issues',
        testId: 'ws-quick-issues',
      },
      {
        label: t('nav.board'),
        path: `/w/${workspace.slug}/board`,
        icon: 'board',
        testId: 'ws-quick-board',
      },
      {
        label: t('nav.members'),
        path: `/w/${workspace.slug}/members`,
        icon: 'user',
        testId: 'ws-quick-members',
      },
    ];
  return (
    <section className="mesh-ws-home" aria-label={t('workspace.homeTitle')}>
      <div className="mesh-ws-home__page-title" data-testid="ws-home-name">
        <PageHeader title={workspace.name} />
      </div>
      <p className="mesh-ws-home__meta" data-testid="ws-home-meta">
        {t('workspace.homeSlug', { slug: workspace.slug })}
        {' · '}
        {t('workspace.homeRole', { role: roleLabel })}
        {' · '}
        {t('workspace.homeLocale', { locale: defaultLocale })}
      </p>

      <ul className="mesh-ws-home__facts">
        <li>
          <span className="mesh-ws-home__fact-label">{t('workspace.slugLabel')}</span>
          <span className="mesh-ws-home__fact-value">{workspace.slug}</span>
        </li>
        <li>
          <span className="mesh-ws-home__fact-label">{t('workspace.localeLabel')}</span>
          <span className="mesh-ws-home__fact-value">{defaultLocale}</span>
        </li>
        <li>
          <span className="mesh-ws-home__fact-label">{t('workspace.timezoneLabel')}</span>
          <span className="mesh-ws-home__fact-value">{workspace.timezone}</span>
        </li>
        <li>
          <span className="mesh-ws-home__fact-label">
            {t('roles.roleLabel', { name: workspace.name })}
          </span>
          <Badge tone={isAdmin ? 'accent' : 'neutral'}>{roleLabel}</Badge>
        </li>
      </ul>

      <nav className="mesh-ws-home__quick-nav" aria-label={t('workspace.homeTitle')}>
        <div className="mesh-ws-home__quick-grid">
          {quickLinks.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className="mesh-ws-home__quick-link"
              data-testid={item.testId}
            >
              <Icon name={item.icon} size={20} />
              <span>{item.label}</span>
              <Icon name="chevron-right" size={16} className="mesh-ws-home__quick-arrow" />
            </Link>
          ))}
        </div>
      </nav>

      {isAdmin ? (
        <Link
          to={`/w/${workspace.slug}/settings`}
          className={buttonClasses('secondary', 'md', 'mesh-ws-home__settings-link')}
          data-testid="ws-settings-link"
        >
          <Icon name="settings" size={16} />
          {t('workspace.settingsEntry')}
        </Link>
      ) : (
        <p className="mesh-ws-home__hint">{t('workspace.settingsHiddenHint')}</p>
      )}
    </section>
  );
}
