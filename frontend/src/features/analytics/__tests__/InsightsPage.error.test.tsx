/** Insights 复用 WorkspaceGate：工作区解析失败时不启动 analytics 请求。 */
import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiError, type MeshApiClient } from '../../../api';
import { renderWithProviders } from '../../../test-utils/render';
import { WorkspaceProvider } from '../../../workspace/WorkspaceProvider';
import { InsightsPage } from '../InsightsPage';

describe('InsightsPage workspace gate error', () => {
  it('renders the workspace error state without mounting the dashboard reader', async () => {
    const workspaceClient = {
      request: vi.fn(async () => {
        throw new MeshApiError({ status: 503, code: 'internal_error', message: 'workspace down' });
      }),
      list: vi.fn(async () => ({ data: [], next_cursor: null })),
    } as unknown as MeshApiClient;

    renderWithProviders(
      <WorkspaceProvider slug="ws" client={workspaceClient}>
        <InsightsPage />
      </WorkspaceProvider>,
      { route: '/w/ws/insights' },
    );

    expect(await screen.findByTestId('ws-error')).toBeInTheDocument();
    expect(screen.queryByTestId('insights-content')).toBeNull();
  });
});
