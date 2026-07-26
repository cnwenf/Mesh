/**
 * AttachmentPanel 渲染测试(attachment.md §4.1/§4.3/§4.4):
 * 图片网格 vs 文件卡片、扫描中占位隐藏下载、agent「AI」徽标、拒绝态、空态、
 * 缩略图签名 URL 加载、下载/删除/复制链接动作。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { AttachmentPanel } from '../components/AttachmentPanel';
import type { Attachment } from '../types';

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
    id: 'att-1',
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
    download_url: '/api/v1/attachments/att-1/download',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function listRoute(items: Attachment[]): Route {
  return {
    match: (url) => url.includes('/api/v1/issues/iss-1/attachments'),
    response: () => fakeResponse({ body: { data: items, next_cursor: null } }),
  };
}

const thumbRoute: Route = {
  match: (url) => url.includes('/thumbnail'),
  response: () => fakeResponse({ body: { data: { url: 'http://cdn/thumb.png', size: 'md', expires_at: 'x' } } }),
};

const downloadRoute: Route = {
  match: (url) => url.includes('/download'),
  response: () => fakeResponse({ body: { data: { url: 'http://cdn/dl', file_name: 'f', expires_at: 'x' } } }),
};

function deleteRoute(): Route {
  return {
    match: (url, method) => method === 'DELETE' && url.includes('/api/v1/attachments/'),
    response: () => fakeResponse({ body: { data: { id: 'att-1', deleted: true } } }),
  };
}

function renderPanel(routes: Route[]): { calls: Array<{ url: string; method: string }> } {
  const { impl, calls } = makeFetch(routes);
  vi.stubGlobal('fetch', impl);
  const client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
  renderWithProviders(<AttachmentPanel workspaceId="ws-1" issueId="iss-1" client={client} />);
  return { calls };
}

beforeEach(() => vi.unstubAllGlobals());
afterEach(() => vi.unstubAllGlobals());

describe('AttachmentPanel', () => {
  it('renders an image grid for images and a card list for files', async () => {
    renderPanel([
      listRoute([
        att({ id: 'img-1', file_name: 'shot.png', mime_type: 'image/png', extension: 'png', is_image: true, thumbnail_url: '/api/v1/attachments/img-1/thumbnail?size=md' }),
        att({ id: 'file-1', file_name: 'report.pdf' }),
      ]),
      thumbRoute,
    ]);
    expect(await screen.findByTestId('attachments-grid')).toBeTruthy();
    expect(screen.getByTestId('attachments-files')).toBeTruthy();
    expect(screen.getByText('report.pdf')).toBeTruthy();
  });

  it('loads the signed thumbnail url into the image', async () => {
    renderPanel([
      listRoute([att({ id: 'img-1', file_name: 'shot.png', is_image: true, mime_type: 'image/png', extension: 'png', thumbnail_url: '/x' })]),
      thumbRoute,
    ]);
    const img = await screen.findByAltText('shot.png');
    await waitFor(() => expect((img as HTMLImageElement).src).toBe('http://cdn/thumb.png'));
  });

  it('hides the download action while scanning and shows the placeholder', async () => {
    renderPanel([listRoute([att({ id: 'file-1', scan_status: 'pending' })])]);
    expect(await screen.findByTestId('attachment-scanning-file-1')).toBeTruthy();
    expect(screen.queryByTestId('attachment-download-file-1')).toBeNull();
  });

  it('shows a rejected note and no download for infected files', async () => {
    renderPanel([listRoute([att({ id: 'file-1', scan_status: 'infected' })])]);
    expect(await screen.findByTestId('attachment-rejected-file-1')).toBeTruthy();
    expect(screen.queryByTestId('attachment-download-file-1')).toBeNull();
  });

  it('marks agent uploads with an AI badge', async () => {
    renderPanel([
      listRoute([
        att({ id: 'file-1', uploader: { id: 'mem-2', member_type: 'agent', display_name: 'code-reviewer' } }),
      ]),
    ]);
    expect(await screen.findByTestId('attachment-ai-file-1')).toBeTruthy();
  });

  it('renders the empty state when there are no attachments', async () => {
    renderPanel([listRoute([])]);
    expect(await screen.findByTestId('attachments-empty')).toBeTruthy();
  });

  it('requests a signed download url when download is clicked', async () => {
    const { calls } = renderPanel([listRoute([att({ id: 'file-1' })]), downloadRoute]);
    const button = await screen.findByTestId('attachment-download-file-1');
    fireEvent.click(button);
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/attachments/file-1/download'))).toBe(true),
    );
  });

  it('deletes an attachment optimistically', async () => {
    const { calls } = renderPanel([listRoute([att({ id: 'file-1' })]), deleteRoute()]);
    const button = await screen.findByTestId('attachment-delete-file-1');
    fireEvent.click(button);
    await waitFor(() => expect(screen.queryByTestId('attachment-file-file-1')).toBeNull());
    expect(calls.some((c) => c.method === 'DELETE')).toBe(true);
  });

  it('copies the signed download link to the clipboard', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderPanel([listRoute([att({ id: 'file-1' })]), downloadRoute]);
    const button = await screen.findByTestId('attachment-copy-file-1');
    fireEvent.click(button);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('http://cdn/dl'));
  });

  it('opens the lightbox with the original image and closes it', async () => {
    renderPanel([
      listRoute([att({ id: 'img-1', file_name: 'shot.png', is_image: true, mime_type: 'image/png', extension: 'png', thumbnail_url: '/x' })]),
      thumbRoute,
      downloadRoute,
    ]);
    fireEvent.click(await screen.findByTestId('attachment-thumb-img-1'));
    // 灯箱加载原图(经 download 端点签名);缩略图与灯箱图同名,断言出现指向签名原图者。
    await waitFor(() => {
      const images = screen.getAllByAltText('shot.png') as HTMLImageElement[];
      expect(images.some((img) => img.src === 'http://cdn/dl')).toBe(true);
    });
  });
});
