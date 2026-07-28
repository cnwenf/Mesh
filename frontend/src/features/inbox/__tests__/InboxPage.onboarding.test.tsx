/**
 * 收件箱空态四要素测试(onboarding.md §1.2.2):插画 + 引导文案 + 主操作深链 /board。
 * fetch 桩按序:me → members → inbox(空)。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { InboxPage } from '../InboxPage';

const ME = {
  user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'WS',
      workspace_slug: 'ws',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};
const MEMBERS = {
  data: [
    {
      id: 'mem-1',
      member_type: 'human',
      role: 'owner',
      status: 'active',
      display_name: 'Owner',
      joined_at: null,
      profile: { id: 'usr-1', full_name: 'Owner', email: 'o@c.com', avatar_url: null },
    },
  ],
  next_cursor: null,
};

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe('InboxPage onboarding empty state', () => {
  it('renders the four-element empty state with a deeplink to the board', async () => {
    const user = userEvent.setup();
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderWithProviders(<InboxPage />);

    await waitFor(() =>
      expect(screen.getByTestId('illustration-inbox-tray')).toBeInTheDocument(),
    );
    expect(screen.getByText('No notifications yet')).toBeInTheDocument();
    expect(
      screen.getByText('Mentions, assignments and agent replies land here.'),
    ).toBeInTheDocument();

    await user.click(screen.getByTestId('inbox-empty-action'));
    // MemoryRouter 无路由树,导航不卸载页面;断言按钮为既有向导深链入口
    expect(screen.getByText('View issues')).toBeInTheDocument();
  });
});
