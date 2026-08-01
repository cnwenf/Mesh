/**
 * ErrorBoundary/ErrorPage — 捕获子树抛错呈现 ErrorPage;重试清除边界状态后恢复渲染。
 */
import { fireEvent, screen } from '@testing-library/react';
import { afterAll, afterEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { ErrorBoundary, ErrorPage } from '../pages/ErrorPage';

let shouldThrow = true;
function Bomb(): React.JSX.Element {
  if (shouldThrow) throw new Error('boom');
  return <div data-testid="bomb-ok">recovered</div>;
}

const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

describe('ErrorBoundary / ErrorPage', () => {
  afterEach(() => {
    shouldThrow = true;
  });

  afterAll(() => {
    consoleError.mockRestore();
  });

  it('ErrorPage 独立渲染(无 onRetry 时无重试按钮)', () => {
    renderWithProviders(<ErrorPage />);
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.queryByTestId('errorpage-retry')).not.toBeInTheDocument();
  });

  it('子树抛错时呈现 ErrorPage', () => {
    renderWithProviders(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.queryByTestId('bomb-ok')).not.toBeInTheDocument();
    expect(screen.getByTestId('errorpage-impact')).toHaveTextContent('workspace');
    expect(screen.getByTestId('errorpage-home')).toHaveAttribute('href', '/');
  });

  it('重试清除边界状态,子树恢复渲染', () => {
    renderWithProviders(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId('errorpage-retry')).toBeInTheDocument();
    shouldThrow = false;
    fireEvent.click(screen.getByTestId('errorpage-retry'));
    expect(screen.getByTestId('bomb-ok')).toBeInTheDocument();
    expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument();
  });
});
