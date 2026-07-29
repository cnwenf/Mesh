/**
 * InsightsPage 边界分支补测(per-file 门禁 B2):覆盖 fetchMe 解析工作区失败时的
 * `.catch` 回调(渲染 error 态)。该路径需 members/api 的 fetchMe 抛错,故单独成文件
 * 以隔离 vi.mock(主 InsightsPage.test 走真实 catalog + 模块级 api mock 的成功路径)。
 */
import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { InsightsPage } from '../InsightsPage';

vi.mock('../../members/api', () => ({
  // fetchMe 失败 → 命中 useEffect 的 .catch 回调(setError)
  fetchMe: vi.fn(async () => {
    throw new Error('me down');
  }),
  activeWorkspace: vi.fn(() => null),
}));

describe('InsightsPage fetchMe error path', () => {
  it('renders the error state when the workspace lookup fails', async () => {
    renderWithProviders(<InsightsPage />, { route: '/insights' });
    await waitFor(() => {
      expect(screen.getByText('Analytics unavailable')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('insights-throughput')).toBeNull();
  });
});
