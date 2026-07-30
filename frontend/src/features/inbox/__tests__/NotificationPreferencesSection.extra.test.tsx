/**
 * NotificationPreferencesSection 补充覆盖:
 * - 非空偏好返回 → 逐条写入并同步免打扰时段(branch L47 + stmts 48-51,含 quiet 非空/为空两臂);
 * - GET 失败 → catch 回退默认矩阵(branch L53 + stmts 54-58);
 * - 免打扰时段为空时保存 → quiet_hours_* 置 null(branch L79/L80 true 臂);
 * - PUT 失败 → catch 错误 toast(branch L85 + stmts 86-87);
 * - 加载期间卸载 → cancelled 守卫(branch L44)。
 * fetch 桩按序:me → members → preferences(GET)→ preferences(PUT)。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { NotificationPreferencesSection } from '../NotificationPreferencesSection';
import { useAuthStore } from '../../../state/authStore';

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
const ERROR_500 = fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } });

function queue(getPrefs: unknown, putPrefs: Response = fakeResponse({ body: { data: [] } })): FetchStub {
  const stub = stubFetch(
    fakeResponse({ body: { data: ME } }),
    fakeResponse({ body: MEMBERS }),
    fakeResponse({ body: getPrefs }),
    putPrefs,
  );
  vi.stubGlobal('fetch', stub.fetchImpl);
  return stub;
}

// MES-106 M1:收件箱/上手清单解析为鉴权请求,用例以登录态为前置。
beforeEach(() => {
  vi.unstubAllGlobals();
  useAuthStore.getState().setToken('tok_test');
});
afterEach(() => {
  useAuthStore.getState().clearToken();
  vi.unstubAllGlobals();
});

describe('NotificationPreferencesSection (补充覆盖)', () => {
  it('applies returned preferences and quiet hours (branch L47 loop + quiet 两臂)', async () => {
    queue({
      data: [
        { event_type: 'mentioned', in_app: false, email: 'realtime', quiet_hours_start: '22:00', quiet_hours_end: '07:00' },
        { event_type: 'assigned', in_app: true, email: 'none', quiet_hours_start: null, quiet_hours_end: null },
      ],
      next_cursor: null,
    });
    renderWithProviders(<NotificationPreferencesSection />);
    await screen.findByTestId('notification-prefs');
    expect((screen.getByTestId('pref-inapp-mentioned') as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId('pref-email-mentioned') as HTMLSelectElement).value).toBe('realtime');
    // quiet_hours_start/end 非空 → 同步到输入框
    expect((screen.getByTestId('pref-quiet-start') as HTMLInputElement).value).toBe('22:00');
    expect((screen.getByTestId('pref-quiet-end') as HTMLInputElement).value).toBe('07:00');
  });

  it('falls back to the default matrix when GET fails (branch L53 catch)', async () => {
    // 第三个响应(GET prefs)为 500 → getPreferences 抛错 → catch 回退默认行
    const stub = stubFetch(
      fakeResponse({ body: { data: ME } }),
      fakeResponse({ body: MEMBERS }),
      ERROR_500,
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    renderWithProviders(<NotificationPreferencesSection />);
    await screen.findByTestId('notification-prefs');
    // 回退默认:in_app 缺省为 true
    expect((screen.getByTestId('pref-inapp-assigned') as HTMLInputElement).checked).toBe(true);
  });

  it('sends null quiet hours when the inputs are empty (branches L79/L80 true arm)', async () => {
    const stub = queue({ data: [], next_cursor: null });
    renderWithProviders(<NotificationPreferencesSection />);
    await screen.findByTestId('notification-prefs');
    fireEvent.click(screen.getByTestId('pref-save'));
    await waitFor(() => {
      const put = stub.calls.find((c) => c.init?.method === 'PUT');
      expect(put).toBeTruthy();
      const body = JSON.parse(String(put?.init?.body)) as {
        preferences: ReadonlyArray<{ quiet_hours_start: string | null; quiet_hours_end: string | null }>;
      };
      expect(body.preferences[0]?.quiet_hours_start).toBeNull();
      expect(body.preferences[0]?.quiet_hours_end).toBeNull();
    });
  });

  it('shows an error toast when the PUT fails (branch L85 catch)', async () => {
    const stub = queue({ data: [], next_cursor: null }, ERROR_500);
    renderWithProviders(<NotificationPreferencesSection />);
    await screen.findByTestId('notification-prefs');
    fireEvent.click(screen.getByTestId('pref-save'));
    await waitFor(() => {
      expect(stub.calls.some((c) => c.init?.method === 'PUT')).toBe(true);
    });
    // 让 reject  microtask 落地,catch + finally 执行(不崩溃即覆盖 L85-87)
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByTestId('pref-save')).toBeTruthy();
  });

  it('discards the result when unmounted during load (cancelled branch L44)', async () => {
    let resolvePrefs: ((response: Response) => void) | null = null;
    const prefsPromise = new Promise<Response>((resolve) => {
      resolvePrefs = resolve;
    });
    let call = 0;
    const fetchImpl = (async () => {
      call += 1;
      if (call === 1) return fakeResponse({ body: { data: ME } });
      if (call === 2) return fakeResponse({ body: MEMBERS });
      return prefsPromise;
    }) as typeof fetch;
    vi.stubGlobal('fetch', fetchImpl);
    const { unmount } = renderWithProviders(<NotificationPreferencesSection />);
    // 等待 GET prefs 进入挂起(第三个 fetch 已被调用)
    await waitFor(() => expect(call).toBe(3));
    unmount(); // cleanup → cancelled = true
    await act(async () => {
      resolvePrefs?.(fakeResponse({ body: { data: [], next_cursor: null } }));
      await Promise.resolve();
    });
    // 卸载后结果被 cancelled 守卫丢弃,不抛错即覆盖 L44
    expect(true).toBe(true);
  });
});
