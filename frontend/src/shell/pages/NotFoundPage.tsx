/**
 * 404 页(路由 `*`):标题/说明 + 回首页链接。
 */
import { Link } from 'react-router';
import { useT } from '../../i18n';

export function NotFoundPage(): React.JSX.Element {
  const t = useT();
  return (
    <main className="mesh-page mesh-page--centered" role="alert">
      <p className="mesh-page__code" aria-hidden="true">
        404
      </p>
      <h1 className="mesh-page__title">{t('notFound.title')}</h1>
      <p className="mesh-page__description">{t('notFound.description')}</p>
      <Link data-testid="notfound-home" className="mesh-page__link" to="/">
        {t('notFound.backHome')}
      </Link>
    </main>
  );
}
