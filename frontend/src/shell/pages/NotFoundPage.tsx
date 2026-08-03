/**
 * 404 页(路由 `*`):标题/说明 + 首页/可选工作区恢复链接。
 */
import { Link } from 'react-router';
import { safeNextPath } from '../../features/auth';
import { useT } from '../../i18n';
import { MAIN_CONTENT_ID, SkipLink } from '../SkipLink';

export interface NotFoundPageProps {
  /** AppShell 内渲染时使用 section，避免嵌套 main 与重复 skip link。 */
  embedded?: boolean;
  /** 当前工作区概览路径；仅同源 `/w/{slug}` 路径会展示。 */
  workspacePath?: string;
}

const WORKSPACE_OVERVIEW_PATH = /^\/w\/[^/?#]+(?:[?#].*)?$/;

function safeWorkspacePath(raw: string | undefined): string | undefined {
  if (raw === undefined) return undefined;
  const safePath = safeNextPath(raw);
  return WORKSPACE_OVERVIEW_PATH.test(safePath) ? safePath : undefined;
}

export function NotFoundPage(props: NotFoundPageProps): React.JSX.Element {
  const t = useT();
  const workspacePath = safeWorkspacePath(props.workspacePath);
  const content = (
    <>
      <p className="mesh-page__code" aria-hidden="true">
        404
      </p>
      <h1 className="mesh-page__title">{t('notFound.title')}</h1>
      <p className="mesh-page__description">{t('notFound.description')}</p>
      {workspacePath !== undefined ? (
        <Link data-testid="notfound-workspace" className="mesh-page__link" to={workspacePath}>
          {t('workspace.homeTitle')}
        </Link>
      ) : null}
      <Link data-testid="notfound-home" className="mesh-page__link" to="/">
        {t('notFound.backHome')}
      </Link>
    </>
  );

  if (props.embedded === true) {
    return (
      <section
        className="mesh-page mesh-page--centered"
        aria-live="assertive"
        aria-atomic="true"
        data-testid="notfound-page"
      >
        {content}
      </section>
    );
  }

  return (
    <>
      <SkipLink label={t('a11y.skipLink')} />
      <main
        id={MAIN_CONTENT_ID}
        tabIndex={-1}
        className="mesh-page mesh-page--centered"
        aria-live="assertive"
        aria-atomic="true"
        data-testid="notfound-page"
      >
        {content}
      </main>
    </>
  );
}
