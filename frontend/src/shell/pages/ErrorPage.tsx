/**
 * 全局错误兜底(README §6.12 异常态矩阵 retry 行)。
 * ErrorBoundary(类组件,getDerivedStateFromError)捕获子树渲染抛错并呈现 ErrorPage;
 * 「重试」清除边界状态,子树重新渲染(错误持续则再次捕获)。
 */
import { Component } from 'react';
import type { ReactNode } from 'react';
import { Button } from '../../design';
import { useT } from '../../i18n';
import { MAIN_CONTENT_ID, SkipLink } from '../SkipLink';

export interface ErrorPageProps {
  onRetry?: () => void;
}

export function ErrorPage(props: ErrorPageProps): React.JSX.Element {
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
        <h1 className="mesh-page__title">{t('errorPage.title')}</h1>
        <p className="mesh-page__description">{t('errorPage.description')}</p>
        {props.onRetry !== undefined ? (
          <Button data-testid="errorpage-retry" variant="secondary" onClick={props.onRetry}>
            {t('errorPage.retry')}
          </Button>
        ) : null}
      </main>
    </>
  );
}

export interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  private readonly handleRetry = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    if (this.state.error !== null) {
      return <ErrorPage onRetry={this.handleRetry} />;
    }
    return this.props.children;
  }
}
