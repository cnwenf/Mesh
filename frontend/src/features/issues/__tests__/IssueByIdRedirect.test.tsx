/**
 * IssueByIdRedirect — identifier 形态旧书签解析至规范 by-identifier;UUID 直渲染(§3.4)。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import { IssueByIdRedirect } from '../IssueByIdRedirect';

// 隔离 IssueDetailPage 的数据加载:本测仅验路由分发(UUID → 详情组件;identifier → 跳转)。
vi.mock('../IssueDetailPage', () => ({
  IssueDetailPage: () => <div data-testid="issue-detail" />,
}));

function ByIdentifierProbe(): React.JSX.Element {
  const { identifier } = useParams<{ identifier: string }>();
  return <div data-testid="by-identifier">{identifier}</div>;
}

function renderRoute(path: string): void {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/w/:workspaceSlug/issues/by-identifier/:identifier"
          element={<ByIdentifierProbe />}
        />
        <Route path="/w/:workspaceSlug/issues/:issueId" element={<IssueByIdRedirect />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('IssueByIdRedirect(§3.4「解析后」by-identifier)', () => {
  it('identifier 形态(小写旧书签)→ replace 至规范 by-identifier(大写归一)', () => {
    renderRoute('/w/acme/issues/web-124');
    expect(screen.getByTestId('by-identifier').textContent).toBe('WEB-124');
  });

  it('UUID 形态 → 直接渲染 IssueDetailPage(保持应用现行用法,不反向跳 by-identifier)', () => {
    renderRoute('/w/acme/issues/550e8400-e29b-41d4-a716-446655440000');
    expect(screen.getByTestId('issue-detail')).not.toBeNull();
    expect(screen.queryByTestId('by-identifier')).toBeNull();
  });
});
