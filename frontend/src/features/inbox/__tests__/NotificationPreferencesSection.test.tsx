/**
 * NotificationPreferencesSection 组件测试(comment-inbox.md §4.2/I11):
 * 矩阵渲染(常规事件 + Agent 执行通知分区)、免打扰输入、保存提交 PUT。
 * fetch 桩按序:me → members → preferences(GET)→ preferences(PUT)。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { NOTIFICATION_TYPES } from '../api';
import { NotificationPreferencesSection } from '../NotificationPreferencesSection';

const ME = {
  user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
  memberships: [
    { workspace_id: 'ws-1', workspace_name: 'WS', workspace_slug: 'ws', role: 'owner', status: 'active', joined_at: null },
  ],
};
const MEMBERS = {
  data: [
    { id: 'mem-1', member_type: 'human', role: 'owner', status: 'active', display_name: 'Owner', joined_at: null,
      profile: { id: 'usr-1', full_name: 'Owner', email: 'o@c.com', avatar_url: null } },
  ],
  next_cursor: null,
};

function queue(): FetchStub {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: { data: [], next_cursor: null } }),
    fakeResponse({ body: { data: [] } }),
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe('NotificationPreferencesSection', () => {
  it('renders a row per notification type with an Agent execution section', async () => {
    queue();
    renderWithProviders(<NotificationPreferencesSection />);
    await screen.findByTestId('notification-prefs');
    for (const type of NOTIFICATION_TYPES) {
      expect(screen.getByTestId(`pref-row-${type}`)).toBeTruthy();
    }
    expect(screen.getByTestId('pref-inapp-execution_finished')).toBeTruthy();
  });

  it('toggles in-app and changes email policy', async () => {
    queue();
    renderWithProviders(<NotificationPreferencesSection />);
    await screen.findByTestId('notification-prefs');
    const checkbox = screen.getByTestId('pref-inapp-assigned') as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(false);
    fireEvent.change(screen.getByTestId('pref-email-assigned'), { target: { value: 'realtime' } });
    expect((screen.getByTestId('pref-email-assigned') as HTMLSelectElement).value).toBe('realtime');
  });

  it('saves all preferences via PUT including quiet hours', async () => {
    const stub = queue();
    renderWithProviders(<NotificationPreferencesSection />);
    await screen.findByTestId('notification-prefs');
    fireEvent.change(screen.getByTestId('pref-quiet-start'), { target: { value: '22:00' } });
    fireEvent.change(screen.getByTestId('pref-quiet-end'), { target: { value: '08:00' } });
    fireEvent.click(screen.getByTestId('pref-save'));
    await waitFor(() => {
      const put = stub.calls.find((c) => c.init?.method === 'PUT');
      expect(put).toBeTruthy();
      const body = JSON.parse(String(put?.init?.body)) as { preferences: unknown[] };
      expect(body.preferences.length).toBe(NOTIFICATION_TYPES.length);
    });
  });
});
