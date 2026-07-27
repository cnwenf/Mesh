/**
 * AttachmentComposer 覆盖补强:回形针触发选择器、拖拽进入/离开切换拖放态、失败重试重新入队、
 * 扫描中卡片占位、无文件粘贴/拖放为空操作。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { AttachmentComposer } from '../components/AttachmentComposer';
import { DEFAULT_MAX_FILE_BYTES } from '../useAttachmentUploader';

function installXhr(autoRespond: boolean): void {
  class Xhr {
    upload: { onprogress: ((e: unknown) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    status = 200;
    responseHeaders: Record<string, string> = {};
    open() {}
    setRequestHeader() {}
    getResponseHeader(name: string) {
      return this.responseHeaders[name] ?? null;
    }
    send() {
      if (autoRespond) {
        queueMicrotask(() => {
          this.responseHeaders.ETag = '"e"';
          this.onload?.();
        });
      }
    }
    abort() {
      this.onabort?.();
    }
  }
  vi.stubGlobal('XMLHttpRequest', Xhr);
}

let uuidCounter = 0;
function installCrypto(): void {
  vi.stubGlobal('crypto', {
    subtle: { digest: vi.fn(async () => new Uint8Array([9]).buffer) },
    randomUUID: () => `uuid-${uuidCounter++}`,
  });
}

if (typeof Blob.prototype.arrayBuffer !== 'function') {
  Blob.prototype.arrayBuffer = function arrayBuffer(this: Blob): Promise<ArrayBuffer> {
    return Promise.resolve(new ArrayBuffer(this.size));
  };
}

interface Route {
  match: (url: string, method: string) => boolean;
  response: () => Response;
}

function makeFetch(routes: Route[]): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const route = routes.find((r) => r.match(url, method));
    return route
      ? route.response()
      : fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
}

const uploadRequestRoute = (): Route => ({
  match: (url) => url.includes('/upload-requests'),
  response: () =>
    fakeResponse({
      status: 201,
      body: {
        data: {
          id: 'att-1',
          upload_status: 'pending',
          blob_id: null,
          scan_status: 'pending',
          mime_type: 'image/png',
          is_image: true,
          upload: { method: 'PUT', url: 'http://storage/put', headers: {}, expires_at: 'x' },
          limits: { max_file_bytes: DEFAULT_MAX_FILE_BYTES },
        },
      },
    }),
});

const completeRoute = (scanStatus: 'clean' | 'pending'): Route => ({
  match: (url) => url.includes('/complete'),
  response: () =>
    fakeResponse({
      body: {
        data: {
          id: 'att-1',
          blob_id: 'blob-1',
          file_name: 'a.png',
          file_size: 8,
          mime_type: 'image/png',
          extension: 'png',
          is_image: true,
          image_width: 1,
          image_height: 1,
          scan_status: scanStatus,
          upload_status: 'completed',
          uploader: null,
          links: [],
          thumbnail_url: null,
          download_url: '/api/v1/attachments/att-1/download',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        },
      },
    }),
});

const serverErrorRoute: Route = {
  match: (url) => url.includes('/upload-requests'),
  response: () => fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } }),
};

function renderComposer(routes: Route[], autoRespond = true) {
  installXhr(autoRespond);
  vi.stubGlobal('fetch', makeFetch(routes));
  const client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
  renderWithProviders(
    <AttachmentComposer workspaceId="ws-1" linkTo={{ type: 'issue', id: 'iss-1' }} client={client} />,
  );
}

function pngFile(name = 'a.png'): File {
  return new File([new Uint8Array(8)], name, { type: 'image/png' });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  uuidCounter = 0;
  installCrypto();
});
afterEach(() => vi.unstubAllGlobals());

describe('AttachmentComposer coverage', () => {
  it('clicking the paperclip opens the file picker', () => {
    renderComposer([uploadRequestRoute(), completeRoute('clean')]);
    const input = screen.getByTestId('attachment-file-input') as HTMLInputElement;
    const clickSpy = vi.spyOn(input, 'click');
    fireEvent.click(screen.getByTestId('attachment-paperclip'));
    expect(clickSpy).toHaveBeenCalled();
  });

  it('toggles the dragging state on drag over and leave', () => {
    renderComposer([uploadRequestRoute(), completeRoute('clean')]);
    const composer = screen.getByTestId('attachment-composer');
    fireEvent.dragOver(composer);
    expect(composer.className).toContain('mesh-attachments-composer--dragging');
    fireEvent.dragLeave(composer);
    expect(composer.className).not.toContain('mesh-attachments-composer--dragging');
  });

  it('re-queues the file when retrying a failed upload', async () => {
    renderComposer([serverErrorRoute]);
    const input = screen.getByTestId('attachment-file-input') as HTMLInputElement;
    await userEvent.upload(input, pngFile());
    const retry = await screen.findByTestId(/upload-retry-/);
    const before = screen.getAllByTestId(/upload-card-/)[0].getAttribute('data-testid');
    fireEvent.click(retry);
    // 旧错误卡片被取消、文件重新入队(新 localId)
    await waitFor(() => {
      const cards = screen.getAllByTestId(/upload-card-/);
      expect(cards[0].getAttribute('data-testid')).not.toBe(before);
    });
  });

  it('shows a scanning note on the card when the scan is pending after complete', async () => {
    renderComposer([uploadRequestRoute(), completeRoute('pending')]);
    const input = screen.getByTestId('attachment-file-input') as HTMLInputElement;
    await userEvent.upload(input, pngFile());
    expect(await screen.findByText('Scanning. Download will be available when complete.')).toBeTruthy();
  });

  it('ignores a paste without clipboard files', () => {
    renderComposer([uploadRequestRoute(), completeRoute('clean')]);
    fireEvent.paste(screen.getByTestId('attachment-composer'), {});
    expect(screen.queryByTestId('upload-cards')).toBeNull();
  });

  it('ignores a drop without files', () => {
    renderComposer([uploadRequestRoute(), completeRoute('clean')]);
    fireEvent.drop(screen.getByTestId('attachment-composer'), { dataTransfer: { files: [] } });
    expect(screen.queryByTestId('upload-cards')).toBeNull();
  });
});
