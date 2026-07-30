/**
 * AttachmentPanel 覆盖补强:加载失败收敛空态、realtime 帧合并接线、下载/复制/删除失败 toast、
 * 图片网格扫描中/拒绝占位、灯箱下载与关闭、缩略图签名 URL 未就绪占位。
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { ThemeProvider, ToastProvider } from '../../../design';
import { I18nProvider, useT } from '../../../i18n';
import type { MissingReporter } from '../../../i18n';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { AttachmentPanel } from '../components/AttachmentPanel';
import type { Attachment } from '../types';

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

function ToastLayer(props: { children: ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

interface Route {
  match: (url: string, method: string) => boolean;
  response: () => Response;
}

function makeFetch(routes: Route[]): { impl: typeof fetch; calls: Array<{ url: string; method: string }> } {
  const calls: Array<{ url: string; method: string }> = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    const route = routes.find((r) => r.match(url, method));
    return route
      ? route.response()
      : fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
  return { impl, calls };
}

function att(overrides: Partial<Attachment> = {}): Attachment {
  return {
    id: 'file-1',
    blob_id: 'blob-1',
    file_name: 'report.pdf',
    file_size: 2048,
    mime_type: 'application/pdf',
    extension: 'pdf',
    is_image: false,
    image_width: null,
    image_height: null,
    scan_status: 'clean',
    upload_status: 'completed',
    uploader: { id: 'mem-1', member_type: 'human', display_name: 'Alice' },
    links: [],
    thumbnail_url: null,
    download_url: '/api/v1/attachments/file-1/download',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

const listRoute = (items: Attachment[]): Route => ({
  match: (url) => url.includes('/api/v1/issues/iss-1/attachments'),
  response: () => fakeResponse({ body: { data: items, next_cursor: null } }),
});

function renderPanel(routes: Route[]) {
  const { impl, calls } = makeFetch(routes);
  vi.stubGlobal('fetch', impl);
  const client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
  render(
    <MemoryRouter>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <AttachmentPanel workspaceId="ws-1" issueId="iss-1" client={client} />
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
  return { calls };
}

interface FakeRealtime {
  value: RealtimeContextValue;
  subscribe: ReturnType<typeof vi.fn>;
  emit: (frame: RealtimeEventFrame) => void;
}

function makeFakeRealtime(): FakeRealtime {
  const subscribe = vi.fn();
  const unsubscribe = vi.fn();
  let listener: ((frame: RealtimeEventFrame) => void) | null = null;
  const client = {
    subscribe,
    unsubscribe,
    onFrame: (cb: (frame: RealtimeEventFrame) => void) => {
      listener = cb;
      return () => {
        listener = null;
      };
    },
  };
  const value = { state: 'online', client } as unknown as RealtimeContextValue;
  return {
    value,
    subscribe,
    emit: (frame) => {
      listener?.(frame);
    },
  };
}

function renderPanelWithRealtime(routes: Route[], realtime: FakeRealtime) {
  const { impl } = makeFetch(routes);
  vi.stubGlobal('fetch', impl);
  const client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
  render(
    <MemoryRouter>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>
            <RealtimeContext.Provider value={realtime.value}>
              <AttachmentPanel workspaceId="ws-1" issueId="iss-1" client={client} />
            </RealtimeContext.Provider>
          </ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe('AttachmentPanel coverage', () => {
  it('shows an error state with retry when the list request fails (M1, not a silent empty list)', async () => {
    let calls = 0;
    renderPanel([
      {
        match: (url) => url.includes('/attachments'),
        response: () => {
          calls += 1;
          return fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } });
        },
      },
    ]);
    const error = await screen.findByTestId('attachments-error');
    expect(error).toBeTruthy();
    expect(screen.queryByTestId('attachments-empty')).toBeNull();
    // 重试重新拉取列表。
    fireEvent.click(screen.getByTestId('attachments-retry'));
    await waitFor(() => expect(calls).toBeGreaterThanOrEqual(2));
  });

  it('merges realtime processed/deleted frames on the issue channel', async () => {
    const realtime = makeFakeRealtime();
    renderPanelWithRealtime([listRoute([att({ id: 'file-1', scan_status: 'pending' })])], realtime);
    await screen.findByTestId('attachment-file-file-1');
    expect(realtime.subscribe).toHaveBeenCalledWith('issue:iss-1');

    // processed:放行 → 下载按钮出现
    act(() =>
      realtime.emit({
        op: 'event',
        channel: 'issue:iss-1',
        seq: 1,
        event: 'attachment.processed',
        payload: { id: 'file-1', scan_status: 'clean' },
      }),
    );
    expect(await screen.findByTestId('attachment-download-file-1')).toBeTruthy();

    // deleted:从列表移除
    act(() =>
      realtime.emit({
        op: 'event',
        channel: 'issue:iss-1',
        seq: 2,
        event: 'attachment.deleted',
        payload: { id: 'file-1' },
      }),
    );
    await waitFor(() => expect(screen.queryByTestId('attachment-file-file-1')).toBeNull());
  });

  it('surfaces a toast when download fails (quarantine 403)', async () => {
    renderPanel([
      listRoute([att({ id: 'file-1' })]),
      {
        match: (url) => url.includes('/download'),
        response: () => fakeResponse({ status: 403, body: { error: { code: 'scan_pending', message: 'x' } } }),
      },
    ]);
    fireEvent.click(await screen.findByTestId('attachment-download-file-1'));
    expect(
      await screen.findByText('This file is still being scanned. Please try again shortly.'),
    ).toBeTruthy();
  });

  it('surfaces a toast when copying the link fails', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(async () => { throw new Error('denied'); }) },
      configurable: true,
    });
    renderPanel([
      listRoute([att({ id: 'file-1' })]),
      {
        match: (url) => url.includes('/download'),
        response: () => fakeResponse({ body: { data: { url: 'http://cdn/dl', file_name: 'f', expires_at: 'x' } } }),
      },
    ]);
    fireEvent.click(await screen.findByTestId('attachment-copy-file-1'));
    expect(await screen.findByText('Something went wrong. Please try again.')).toBeTruthy();
  });

  it('rolls back and toasts when deletion fails', async () => {
    renderPanel([
      listRoute([att({ id: 'file-1' })]),
      {
        match: (_url, method) => method === 'DELETE',
        response: () => fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
      },
    ]);
    fireEvent.click(await screen.findByTestId('attachment-delete-file-1'));
    // 失败后整区重取,行回归
    expect(await screen.findByTestId('attachment-file-file-1')).toBeTruthy();
    expect(await screen.findByText('An internal error occurred. Please try again.')).toBeTruthy();
  });

  it('renders scanning and rejected placeholder tiles for non-released images', async () => {
    renderPanel([
      listRoute([
        att({ id: 'img-pending', file_name: 'p.png', is_image: true, mime_type: 'image/png', extension: 'png', scan_status: 'pending' }),
        att({ id: 'img-infected', file_name: 'i.png', is_image: true, mime_type: 'image/png', extension: 'png', scan_status: 'infected' }),
      ]),
    ]);
    const scanning = await screen.findByTestId('attachment-scanning-img-pending');
    expect(scanning.textContent).toContain('Scanning');
    // 感染态:危险 tone + 图标 + scanInfected 文案(§4.6;locale=en 渲染实际英文)。
    const infected = screen.getByTestId('attachment-scanning-img-infected');
    expect(infected.textContent).toContain('Security scan blocked this file');
    expect(infected.querySelector('.mesh-attachments__scan-icon')).not.toBeNull();
    expect(infected.className).toContain('mesh-attachments__scan--danger');
  });

  it('shows a placeholder until the thumbnail signed url resolves', async () => {
    renderPanel([
      listRoute([att({ id: 'img-1', file_name: 's.png', is_image: true, mime_type: 'image/png', extension: 'png', thumbnail_url: '/x' })]),
      // 缩略图请求永不解析 → 占位常驻
      { match: (url) => url.includes('/thumbnail'), response: () => new Promise<Response>(() => undefined) as unknown as Response },
    ]);
    const thumb = await screen.findByTestId('attachment-thumb-img-1');
    expect(thumb.querySelector('.mesh-attachments__thumb-placeholder')).toBeTruthy();
  });

  it('downloads from and closes the lightbox', async () => {
    const { calls } = renderPanel([
      listRoute([att({ id: 'img-1', file_name: 's.png', is_image: true, mime_type: 'image/png', extension: 'png', thumbnail_url: '/x' })]),
      { match: (url) => url.includes('/thumbnail'), response: () => fakeResponse({ body: { data: { url: 'http://cdn/t.png', size: 'md', expires_at: 'x' } } }) },
      { match: (url) => url.includes('/download'), response: () => fakeResponse({ body: { data: { url: 'http://cdn/dl', file_name: 's.png', expires_at: 'x' } } }) },
    ]);
    fireEvent.click(await screen.findByTestId('attachment-thumb-img-1'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes('/download')).length).toBeGreaterThan(0));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('renders file-card scan notices with icon + tone; pending hides download, infected/error show next steps (§4.5/§4.6)', async () => {
    renderPanel([
      listRoute([
        att({ id: 'p', file_name: 'p.pdf', scan_status: 'pending' }),
        att({ id: 'i', file_name: 'i.pdf', scan_status: 'infected' }),
        att({ id: 'e', file_name: 'e.pdf', scan_status: 'error' }),
      ]),
    ]);
    // pending:信息 tone + 时钟图标 + 无下载,但删除始终可用(M3)
    const pendingNotice = await screen.findByTestId('attachment-scanning-p');
    expect(pendingNotice.className).toContain('mesh-attachments__scan--info');
    expect(pendingNotice.querySelector('.mesh-attachments__scan-icon')).not.toBeNull();
    expect(screen.queryByTestId('attachment-download-p')).toBeNull();
    expect(screen.getByTestId('attachment-delete-p')).toBeTruthy();
    // infected:危险 tone 卡片 + 「接下来会发生什么」说明
    const infectedCard = await screen.findByTestId('attachment-file-i');
    expect(infectedCard.className).toContain('mesh-attachments__file--danger');
    const infectedNotice = screen.getByTestId('attachment-rejected-i');
    expect(infectedNotice.className).toContain('mesh-attachments__scan--danger');
    expect(infectedNotice.textContent).toContain("It's permanently blocked.");
    // error:危险 tone + 重试指引
    const errorNotice = await screen.findByTestId('attachment-rejected-e');
    expect(errorNotice.textContent).toContain("Security scan couldn't verify this file");
    expect(errorNotice.textContent).toContain('Try uploading again, or contact an admin.');
  });

  it('renders an always-available touch "more" menu mirroring the hover actions (§8.2)', async () => {
    renderPanel([listRoute([att({ id: 'file-1' })])]);
    const trigger = await screen.findByRole('button', { name: 'More actions' });
    fireEvent.click(trigger);
    expect((await screen.findByRole('menuitem', { name: 'Download' })).textContent).toBe('Download');
    expect(screen.getByRole('menuitem', { name: 'Copy link' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Delete' })).toBeTruthy();
  });

  it('omits download/copy from the touch menu when not released', async () => {
    renderPanel([listRoute([att({ id: 'p', scan_status: 'pending' })])]);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions' }));
    expect(await screen.findByRole('menuitem', { name: 'Delete' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Download' })).toBeNull();
  });

  it('falls back for unknown/agent uploaders (avatar initial + display name)', async () => {
    renderPanel([
      listRoute([
        att({ id: 'u-null', uploader: null }),
        att({ id: 'u-agent', uploader: { id: 'a', member_type: 'agent', display_name: null } }),
      ]),
    ]);
    expect(await screen.findByTestId('attachment-avatar-u-null')).toBeTruthy();
    expect(screen.getByTestId('attachment-avatar-u-null').textContent).toBe('?');
    expect(screen.getByTestId('attachment-avatar-u-agent').textContent).toBe('A');
    // 无显示名 → 回落「未知」
    expect(screen.getByTestId('attachment-file-u-null').textContent).toContain('Unknown');
  });

  it('constructs a default client when none is injected', async () => {
    vi.stubGlobal(
      'fetch',
      (async () => fakeResponse({ body: { data: [], next_cursor: null } })) as typeof fetch,
    );
    render(
      <MemoryRouter>
        <ThemeProvider>
          <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
            <ToastLayer>
              <AttachmentPanel workspaceId="ws-1" issueId="iss-1" />
            </ToastLayer>
          </I18nProvider>
        </ThemeProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId('attachments-empty')).toBeTruthy();
  });

  it('copies the stable link through the touch menu', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderPanel([listRoute([att({ id: 'file-1' })])]);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Copy link' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining('/download')));
  });

  it('locates the attachment from the lightbox (closes dialog and scrolls to the tile)', async () => {
    // jsdom 无 scrollIntoView;桩掉以免定位回调抛错(afterEach 统一拆除)。
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    renderPanel([
      listRoute([att({ id: 'img-1', file_name: 's.png', is_image: true, mime_type: 'image/png', extension: 'png', thumbnail_url: '/x' })]),
      { match: (url) => url.includes('/thumbnail'), response: () => fakeResponse({ body: { data: { url: 'http://cdn/t.png', size: 'md', expires_at: 'x' } } }) },
      { match: (url) => url.includes('/download'), response: () => fakeResponse({ body: { data: { url: 'http://cdn/dl', file_name: 's.png', expires_at: 'x' } } }) },
    ]);
    fireEvent.click(await screen.findByTestId('attachment-thumb-img-1'));
    await screen.findByRole('dialog');
    fireEvent.click(await screen.findByTestId('lightbox-locate'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // 等 rAF 定位回调执行(滚动到缩略图条目)
    await waitFor(() => expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled());
  });

  it('invokes download and delete through the touch "more" menu (§8.2)', async () => {
    const { calls } = renderPanel([
      listRoute([att({ id: 'file-1' })]),
      {
        match: (url) => url.includes('/download'),
        response: () => fakeResponse({ body: { data: { url: 'http://cdn/dl', file_name: 'f', expires_at: 'x' } } }),
      },
    ]);
    fireEvent.click(await screen.findByRole('button', { name: 'More actions' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Download' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/download'))).toBe(true));
    // 选择后菜单关闭;重开后经菜单删除
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
  });

  it('shows an actionable downloadFailed toast on a transient (network) download failure (parity §2.22)', async () => {
    renderPanel([
      listRoute([att({ id: 'file-1' })]),
      {
        match: (url) => url.includes('/download'),
        response: (): Response => {
          throw new TypeError('network down');
        },
      },
    ]);
    fireEvent.click(await screen.findByTestId('attachment-download-file-1'));
    expect(await screen.findByText(/Download failed — the link may have expired\. Try again\./)).toBeTruthy();
  });
});
