/**
 * CommentsPanel 评论草稿脏态离开保护测试(L242):
 * 草稿写穿 localStorage 时导航不打扰;存储不可用(隐私模式等)草稿仅驻留内存时,
 * 站内导航弹 stay/discard 确认,防止丢草稿。
 */
import { fireEvent, screen } from '@testing-library/react';
import { Link, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { CommentsPanel } from '../CommentsPanel';

function queueComments(): void {
  const stub = stubFetch(fakeResponse({ body: { data: [], next_cursor: null } }));
  vi.stubGlobal('fetch', stub.fetchImpl);
}

function renderPanelWithExit(): void {
  renderWithProviders(
    <>
      <Routes>
        <Route
          path="/issues/iss-1"
          element={
            <CommentsPanel
              issueId="iss-1"
              workspaceId="ws-1"
              locale="en"
              candidates={[]}
              currentMember={null}
            />
          }
        />
        <Route path="/somewhere" element={<div data-testid="left-page">left-page</div>} />
      </Routes>
      <Link to="/somewhere" data-testid="leave-link">
        leave
      </Link>
    </>,
    { route: '/issues/iss-1' },
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('CommentsPanel draft dirty guard (L242)', () => {
  it('blocks navigation when the draft is memory-only (storage unavailable); stay keeps it', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('private mode');
    });
    queueComments();
    renderPanelWithExit();

    const input = (await screen.findByTestId('composer-input')) as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: '未持久化的草稿' } });
    fireEvent.click(screen.getByTestId('leave-link'));

    expect(await screen.findByTestId('dirty-guard-stay')).toBeTruthy();
    fireEvent.click(screen.getByTestId('dirty-guard-stay'));
    expect(screen.queryByTestId('left-page')).toBeNull();
    expect((screen.getByTestId('composer-input') as HTMLTextAreaElement).value).toBe(
      '未持久化的草稿',
    );
  });

  it('discard leaves the page when the draft is memory-only', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('private mode');
    });
    queueComments();
    renderPanelWithExit();

    const input = (await screen.findByTestId('composer-input')) as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: '要放弃的草稿' } });
    fireEvent.click(screen.getByTestId('leave-link'));

    fireEvent.click(await screen.findByTestId('dirty-guard-discard'));
    expect(await screen.findByTestId('left-page')).toBeTruthy();
  });

  it('persisted drafts do not block navigation', async () => {
    queueComments();
    renderPanelWithExit();

    const input = (await screen.findByTestId('composer-input')) as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: '已持久化的草稿' } });
    fireEvent.click(screen.getByTestId('leave-link'));

    expect(await screen.findByTestId('left-page')).toBeTruthy();
    expect(screen.queryByTestId('dirty-guard-stay')).toBeNull();
  });
});
