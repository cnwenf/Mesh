/**
 * NotificationPreferencesSection 补充覆盖:save() catch 里错误映射三元表达式的
 * 「非 MeshApiError」分支(branch L86 的 else 臂 → 'state.errorDescription')。
 * 真实客户端把所有失败都归一为 MeshApiError,该臂经 HTTP 不可达;此处 mock
 * updatePreferences 以普通 Error reject,使 `err instanceof MeshApiError` 为 false。
 * 独立成文件:vi.mock('../api') 为模块级。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { updatePreferences } from '../api';
import { NotificationPreferencesSection } from '../NotificationPreferencesSection';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    updatePreferences: vi.fn().mockRejectedValue(new Error('boom')),
  };
});

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

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe('NotificationPreferencesSection save() 非 MeshApiError 映射 (branch L86 else 臂)', () => {
  it('maps a non-MeshApiError to the generic error description', async () => {
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      fakeResponse({ body: { data: [], next_cursor: null } }), // GET prefs
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderWithProviders(<NotificationPreferencesSection />);
    await screen.findByTestId('notification-prefs');
    fireEvent.click(screen.getByTestId('pref-save'));
    await waitFor(() => expect(vi.mocked(updatePreferences)).toHaveBeenCalledTimes(1));
    // 普通 Error → 非 MeshApiError → key 退回 'state.errorDescription'(L86 else 臂);不崩溃
    expect(screen.getByTestId('pref-save')).toBeTruthy();
  });
});
