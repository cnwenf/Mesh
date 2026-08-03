/**
 * 404 页(路由 `*`):标题/说明 + 回首页链接。
 */
import { Link } from 'react-router';
import { useT } from '../../i18n';
import { MAIN_CONTENT_ID, SkipLink } from '../SkipLink';

export function NotFoundPage(): React.JSX.Element {
  const t = useT();
  return (
    <>
      <SkipLink label={t('a11y.skipLink')} />
      <main
        id={MAIN_CONTENT_ID}
        tabIndex={-1}
        className="mesh-page mesh-page--centered"
        aria-live="assertive"
        aria-atomic="true"
      >
        <p className="mesh-page__code" aria-hidden="true">
          404
        </p>
        <h1 className="mesh-page__title">{t('notFound.title')}</h1>
        <p className="mesh-page__description">{t('notFound.description')}</p>
        <Link data-testid="notfound-home" className="mesh-page__link" to="/">
          {t('notFound.backHome')}
        </Link>
      </main>
    </>
  );
}
