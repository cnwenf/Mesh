/**
 * 403 权限不足页：说明影响与恢复方式，并提供安全、可操作的返回出口。
 */
import { Link, useSearchParams } from 'react-router';
import { safeNextPath } from '../../features/auth';
import { useDocumentTitle } from '../../hooks/useDocumentTitle';
import { useT } from '../../i18n';
import { MAIN_CONTENT_ID, SkipLink } from '../SkipLink';
import { SeoMeta } from '../SeoMeta';

export interface ForbiddenPageProps {
  /** 有工作区上下文时返回其概览并打开成员名册；非法或跨站路径会被忽略。 */
  workspacePath?: string;
}

const WORKSPACE_OVERVIEW_PATH = /^\/w\/[^/?#]+$/;

function safeWorkspacePath(raw: string | null | undefined): string | undefined {
  if (raw === null || raw === undefined) return undefined;
  const safePath = safeNextPath(raw);
  return WORKSPACE_OVERVIEW_PATH.test(safePath) ? safePath : undefined;
}

export function ForbiddenPage(props: ForbiddenPageProps): React.JSX.Element {
  const t = useT();
  const [searchParams] = useSearchParams();
  useDocumentTitle(t('state.permissionTitle'));

  const workspacePath = safeWorkspacePath(props.workspacePath ?? searchParams.get('workspace'));

  return (
    <>
      <SeoMeta />
      <SkipLink label={t('a11y.skipLink')} />
      <main
        id={MAIN_CONTENT_ID}
        tabIndex={-1}
        className="mesh-page mesh-page--centered"
        aria-live="assertive"
        aria-atomic="true"
        data-testid="forbidden-page"
      >
        <p className="mesh-page__code" aria-hidden="true">
          403
        </p>
        <h1 className="mesh-page__title">{t('state.permissionTitle')}</h1>
        <p className="mesh-page__description">{t('state.permissionDescription')}</p>
        <p className="mesh-page__description" data-testid="forbidden-contact">
          {t('state.permissionHint')}
        </p>
        {workspacePath !== undefined ? (
          <>
            <Link
              className="mesh-page__link"
              data-testid="forbidden-contact-action"
              to={`${workspacePath}/members`}
            >
              {t('state.permissionContactAction')}
            </Link>
            <Link className="mesh-page__link" data-testid="forbidden-workspace" to={workspacePath}>
              {t('workspace.homeTitle')}
            </Link>
          </>
        ) : null}
        <Link className="mesh-page__link" data-testid="forbidden-home" to="/">
          {t('notFound.backHome')}
        </Link>
      </main>
    </>
  );
}
