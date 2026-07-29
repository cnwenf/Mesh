/**
 * ChatPage 聊天上下文组注册(§4.3 S12):['global','chat'] 独占激活 +
 * enter/shift+enter/mod+↑/esc 命令登记与动作可达。
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { resetApiClient } from '../../../api/instance';
import { ToastProvider } from '../../../design';
import { I18nProvider } from '../../../i18n';
import { useShortcutRegistry } from '../../../shortcuts';
import { ChatPage } from '../ChatPage';

const ME = {
  user: { id: 'u1', email: 'u@example.com', display_name: 'U', last_active_workspace_id: null },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'Acme',
      workspace_slug: 'acme',
      role: 'member',
      status: 'active',
      joined_at: null,
    },
  ],
};

function stubBackend(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as unknown as typeof fetch,
  );
  resetApiClient();
}

function renderPage(): void {
  render(
    <I18nProvider requested={null} systemLocales={[]}>
      <ToastProvider regionLabel="notifications">
        <MemoryRouter initialEntries={['/w/acme/chat']}>
          <ChatPage />
        </MemoryRouter>
      </ToastProvider>
    </I18nProvider>,
  );
}

beforeEach(() => {
  useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
});
afterEach(() => {
  vi.unstubAllGlobals();
  resetApiClient();
});

describe('ChatPage 聊天上下文组(§4.3 S12)', () => {
  it("上下文独占激活 ['global','chat']", async () => {
    stubBackend();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('chat-page')).toBeInTheDocument());
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global', 'chat']);
  });

  it('四条聊天快捷键登记且动作可达(发送/换行/编辑上一条/失焦)', async () => {
    stubBackend();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('chat-page')).toBeInTheDocument());
    const shortcuts = useShortcutRegistry.getState().shortcuts;
    const ids = shortcuts.map((def) => def.id);
    for (const id of ['chat.send', 'chat.newline', 'chat.edit.last', 'chat.blur']) {
      expect(ids).toContain(id);
    }
    // 动作可达(无会话选中时亦安全无副作用;Esc 语义为失焦当前焦点元素)。
    for (const def of shortcuts.filter((item) => item.group === 'chat')) {
      expect(() => act(() => def.run())).not.toThrow();
    }
  });

  it('卸载后上下文复位 [global]', async () => {
    stubBackend();
    const { unmount } = render(
      <I18nProvider requested={null} systemLocales={[]}>
        <ToastProvider regionLabel="notifications">
          <MemoryRouter initialEntries={['/w/acme/chat']}>
            <ChatPage />
          </MemoryRouter>
        </ToastProvider>
      </I18nProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('chat-page')).toBeInTheDocument());
    unmount();
    expect(useShortcutRegistry.getState().activeContexts).toEqual(['global']);
  });
});
