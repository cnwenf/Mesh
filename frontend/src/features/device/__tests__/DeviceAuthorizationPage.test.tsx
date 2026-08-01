/**
 * 设备码授权确认页测试(auth.md §3.1.1 UX 契约):
 * - 未登录 → 登录引导;
 * - 0 工作区 → 禁用批准 + 提示;1 个 → 自动绑定;多个 → 必选(未选不可提交);
 * - 批准绑定「所录入」的码(预填值不参与提交,防钓鱼);
 * - user_code 未命中 → 统一 not_found(不区分原因)。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { DeviceAuthorizationPage } from '../DeviceAuthorizationPage';

const authState = vi.hoisted(() => ({ token: 'access-jwt' as string | null }));
vi.mock('../../../state/authStore', () => ({
  useAuthStore: (selector: (state: { token: string | null }) => unknown) =>
    selector({ token: authState.token }),
}));

const apiState = vi.hoisted(() => ({
  confirmation: null as unknown,
  confirmShouldThrow: false,
  approveShouldThrow: false,
  denyShouldThrow: false,
  approveCalls: [] as { userCode: string; workspaceId: string }[],
  denyCalls: [] as string[],
}));

vi.mock('../../../api/instance', () => ({
  getApiClient: () => ({
    request: (
      _method: string,
      path: string,
      opts?: { query?: Record<string, string>; body?: unknown },
    ) => {
      if (path === '/api/v1/auth/device') {
        if (apiState.confirmShouldThrow) return Promise.reject(new Error('404'));
        return Promise.resolve(apiState.confirmation);
      }
      if (path === '/api/v1/auth/device/approve') {
        if (apiState.approveShouldThrow) return Promise.reject(new Error('approve failed'));
        const body = opts?.body as { user_code: string; workspace_id: string };
        apiState.approveCalls.push({ userCode: body.user_code, workspaceId: body.workspace_id });
        return Promise.resolve({ status: 'approved' });
      }
      if (path === '/api/v1/auth/device/deny') {
        if (apiState.denyShouldThrow) return Promise.reject(new Error('deny failed'));
        const body = opts?.body as { user_code: string };
        apiState.denyCalls.push(body.user_code);
        return Promise.resolve({ status: 'denied' });
      }
      return Promise.reject(new Error(`unexpected path ${path}`));
    },
  }),
}));

function renderPage(initialUrl = '/device'): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <I18nProvider
        workspaceDefaultLocale={null}
        reporter={{ report: () => undefined, reported: [] }}
      >
        <DeviceAuthorizationPage />
      </I18nProvider>
    </MemoryRouter>,
  );
}

const TWO_WS = {
  client_name: 'Mesh CLI',
  requested_scopes: [
    { scope: 'issue:read', description: 'Read issues' },
    { scope: 'issue:write', description: 'Write issues' },
  ],
  workspaces: [
    { id: 'ws-1', slug: 'acme', name: 'Acme', my_role: 'member' },
    { id: 'ws-2', slug: 'beta', name: 'Beta', my_role: 'admin' },
  ],
};

beforeEach(() => {
  authState.token = 'access-jwt';
  apiState.confirmation = null;
  apiState.confirmShouldThrow = false;
  apiState.approveShouldThrow = false;
  apiState.denyShouldThrow = false;
  apiState.approveCalls = [];
  apiState.denyCalls = [];
});

describe('DeviceAuthorizationPage', () => {
  it('prompts to sign in when unauthenticated', () => {
    authState.token = null;
    renderPage();
    // Not signed in → a sign-in link is offered.
    expect(screen.getByRole('link', { name: 'Sign in' })).toBeTruthy();
  });

  it('prefills but keeps the input editable and validates the typed code', async () => {
    apiState.confirmation = TWO_WS;
    renderPage('/device?user_code=AAAA-BBBB');
    const input = screen.getByRole('textbox') as HTMLInputElement;
    expect(input.value).toBe('AAAA-BBBB');
    // The user changes the typed code before confirming.
    fireEvent.change(input, { target: { value: 'CCCC-DDDD' } });
    await waitFor(() => expect(screen.getByRole('note')).toBeTruthy());
    // Multi-workspace: approve disabled until a workspace is chosen.
    // Button order: [submit, approve, deny].
    const buttons = () => screen.getAllByRole('button') as HTMLButtonElement[];
    await waitFor(() => expect(buttons().length).toBe(3));
    expect(buttons()[1].disabled).toBe(true);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'ws-2' } });
    expect(buttons()[1].disabled).toBe(false);
    fireEvent.click(buttons()[1]);
    await waitFor(() => expect(apiState.approveCalls.length).toBe(1));
    // Approves the TYPED code, not the prefilled value (phishing defence).
    expect(apiState.approveCalls[0]).toEqual({ userCode: 'CCCC-DDDD', workspaceId: 'ws-2' });
  });

  it('auto-binds a single workspace', async () => {
    apiState.confirmation = {
      ...TWO_WS,
      workspaces: [{ id: 'ws-1', slug: 'acme', name: 'Acme', my_role: 'member' }],
    };
    renderPage();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'XXXX-YYYY' } });
    fireEvent.click((screen.getAllByRole('button') as HTMLButtonElement[])[0]);
    await waitFor(() => expect(screen.getByText('Acme')).toBeTruthy());
    const approve = (screen.getAllByRole('button') as HTMLButtonElement[])[1];
    expect(approve.disabled).toBe(false);
    fireEvent.click(approve);
    await waitFor(() => expect(apiState.approveCalls.length).toBe(1));
    expect(apiState.approveCalls[0].workspaceId).toBe('ws-1');
  });

  it('disables approval when the approver has no workspace', async () => {
    apiState.confirmation = { ...TWO_WS, workspaces: [] };
    renderPage();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'NNNN-NNNN' } });
    fireEvent.click((screen.getAllByRole('button') as HTMLButtonElement[])[0]);
    // 0 workspaces → alert + approval disabled.
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    expect((screen.getAllByRole('button') as HTMLButtonElement[])[1].disabled).toBe(true);
  });

  it('shows a uniform not-found state (no reason leak)', async () => {
    apiState.confirmShouldThrow = true;
    renderPage();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'ZZZZ-ZZZZ' } });
    fireEvent.click((screen.getAllByRole('button') as HTMLButtonElement[])[0]);
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
  });

  it('deny submits the typed code', async () => {
    apiState.confirmation = TWO_WS;
    renderPage();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'DDDD-EEEE' } });
    fireEvent.click((screen.getAllByRole('button') as HTMLButtonElement[])[0]);
    await waitFor(() => expect(screen.getByRole('note')).toBeTruthy());
    fireEvent.click((screen.getAllByRole('button') as HTMLButtonElement[])[2]);
    await waitFor(() => expect(apiState.denyCalls).toEqual(['DDDD-EEEE']));
    await waitFor(() => expect(screen.getByRole('status')).toBeTruthy());
  });

  it.each([
    ['approve', 'Approval failed — try again.'],
    ['deny', 'Denial failed — try again.'],
  ] as const)('shows the %s request error', async (action, message) => {
    apiState.confirmation = TWO_WS;
    apiState[`${action}ShouldThrow`] = true;
    renderPage();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'FAIL-CODE' } });
    fireEvent.click((screen.getAllByRole('button') as HTMLButtonElement[])[0]);
    await waitFor(() => expect(screen.getByRole('note')).toBeTruthy());
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'ws-1' } });
    fireEvent.click(
      (screen.getAllByRole('button') as HTMLButtonElement[])[action === 'approve' ? 1 : 2],
    );
    expect(await screen.findByText(message)).toBeTruthy();
  });
});
