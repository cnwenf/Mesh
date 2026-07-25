/**
 * 工作区首页(/w/:workspaceSlug)—— 当前工作区概览(workspace.md §4.1)。
 *
 * 呈现工作区名称/slug/我的角色/默认 locale;admin+ 提供设置入口(§6.12 角色可见性)。
 */
import { Link } from 'react-router-dom';
import { useT } from '../../i18n';
import { useWorkspace, WorkspaceGate } from '../WorkspaceProvider';

export function WorkspaceHomePage(): React.JSX.Element {
  return (
    <WorkspaceGate>
      <WorkspaceOverview />
    </WorkspaceGate>
  );
}

function WorkspaceOverview(): React.JSX.Element {
  const { workspace, isAdmin } = useWorkspace();
  const t = useT();
  if (workspace === null) return <></>;
  const defaultLocale =
    typeof workspace.settings.default_locale === 'string' ? workspace.settings.default_locale : 'en';
  return (
    <section className="mesh-ws-home" aria-label={t('workspace.homeTitle')}>
      <h1 data-testid="ws-home-name">{workspace.name}</h1>
      <p className="mesh-ws-home__meta" data-testid="ws-home-meta">
        {t('workspace.homeSlug', { slug: workspace.slug })}
        {' · '}
        {t('workspace.homeRole', { role: t(`roles.${workspace.my_role}`) })}
        {' · '}
        {t('workspace.homeLocale', { locale: defaultLocale })}
      </p>
      {isAdmin ? (
        <Link to={`/w/${workspace.slug}/settings`} data-testid="ws-settings-link">
          {t('workspace.settingsEntry')}
        </Link>
      ) : (
        <p className="mesh-ws-home__hint">{t('workspace.settingsHiddenHint')}</p>
      )}
    </section>
  );
}
