/**
 * 全局错误兜底(README §6.12 异常态矩阵 retry 行)。
 * ErrorBoundary(类组件,getDerivedStateFromError)捕获子树渲染抛错并呈现 ErrorPage;
 * 「重试」清除边界状态,子树重新渲染(错误持续则再次捕获)。
 */
import { Component } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { Button, ErrorState } from '../../design';
import { useT } from '../../i18n';

export interface ErrorPageProps {
  onRetry?: () => void;
}

export function ErrorPage(props: ErrorPageProps): React.JSX.Element {
  const t = useT();
  return (
    <main className="mesh-page mesh-page--centered" role="alert">
      <ErrorState
        title={t('errorPage.title')}
        titleElement="h1"
        description={t('errorPage.description')}
        impact={<span data-testid="errorpage-impact">{t('errorPage.impact')}</span>}
        action={
          <div className="mesh-page__error-actions">
            {props.onRetry !== undefined ? (
              <Button data-testid="errorpage-retry" variant="secondary" onClick={props.onRetry}>
                {t('errorPage.retry')}
              </Button>
            ) : null}
            <Link className="mesh-page__link" data-testid="errorpage-home" to="/">
              {t('errorPage.backHome')}
            </Link>
          </div>
        }
      />
    </main>
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
