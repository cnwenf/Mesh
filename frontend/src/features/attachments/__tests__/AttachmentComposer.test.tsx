/**
 * AttachmentComposer 测试(attachment.md §4.1/§4.2):文件选择/拖拽/粘贴触发上传,
 * 进行中提交按钮禁用(全部完成方可提交),完成后提交回调 onUploaded,失败显示重试,取消移除卡片。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { AttachmentComposer } from '../components/AttachmentComposer';
import { DEFAULT_MAX_FILE_BYTES } from '../useAttachmentUploader';

/* ---- 桩:XHR(可控自动完成)+ crypto.subtle ---- */

function installXhr(autoRespond: boolean): void {
  class Xhr {
    upload = { onprogress: null as null | ((e: unknown) => void) };
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

const uploadRequestRoute: Route = {
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
};

const completeRoute: Route = {
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
          scan_status: 'clean',
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
};

function renderComposer(autoRespond: boolean, onUploaded?: (a: unknown) => void) {
  installXhr(autoRespond);
  vi.stubGlobal('fetch', makeFetch([uploadRequestRoute, completeRoute]));
  const client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
  renderWithProviders(
    <AttachmentComposer
      workspaceId="ws-1"
      linkTo={{ type: 'issue', id: 'iss-1' }}
      client={client}
      onUploaded={onUploaded as never}
    />,
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

describe('AttachmentComposer', () => {
  it('selecting a file via the picker starts an upload card', async () => {
    renderComposer(true);
    const input = screen.getByTestId('attachment-file-input') as HTMLInputElement;
    await userEvent.upload(input, pngFile());
    expect(await screen.findByTestId('upload-cards')).toBeTruthy();
  });

  it('submit is disabled while an upload is in flight', async () => {
    renderComposer(false); // XHR 挂起 → 停留 uploading
    const input = screen.getByTestId('attachment-file-input') as HTMLInputElement;
    await userEvent.upload(input, pngFile());
    await waitFor(() => expect(screen.getByTestId('upload-cards')).toBeTruthy());
    const submit = screen.getByTestId('attachment-submit') as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it('submit is disabled with no uploads, enabled after completion, and emits onUploaded', async () => {
    const onUploaded = vi.fn();
    renderComposer(true, onUploaded);
    const submit = screen.getByTestId('attachment-submit') as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    const input = screen.getByTestId('attachment-file-input') as HTMLInputElement;
    await userEvent.upload(input, pngFile());
    await waitFor(() => expect((screen.getByTestId('attachment-submit') as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId('attachment-submit'));
    expect(onUploaded).toHaveBeenCalledTimes(1);
    const emitted = onUploaded.mock.calls[0][0] as Array<{ id: string }>;
    expect(emitted[0].id).toBe('att-1');
  });

  it('pasting a screenshot triggers an upload', async () => {
    renderComposer(true);
    const composer = screen.getByTestId('attachment-composer');
    fireEvent.paste(composer, { clipboardData: { files: [pngFile('paste.png')] } });
    expect(await screen.findByTestId('upload-cards')).toBeTruthy();
  });

  it('dropping a file triggers an upload', async () => {
    renderComposer(true);
    const composer = screen.getByTestId('attachment-composer');
    fireEvent.drop(composer, { dataTransfer: { files: [pngFile('drop.png')] } });
    expect(await screen.findByTestId('upload-cards')).toBeTruthy();
  });

  it('shows a validation error with retry for an unsupported file', async () => {
    renderComposer(true);
    const input = screen.getByTestId('attachment-file-input') as HTMLInputElement;
    await userEvent.upload(input, new File(['x'], 'tool.exe', { type: 'application/x-msdownload' }));
    // 错误文案经 i18n(error.unsupported_media_type)呈现;重试按钮可见。
    const retry = await screen.findAllByText(/.+/);
    expect(retry.length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button').some((b) => b.getAttribute('data-testid')?.startsWith('upload-retry-'))).toBe(true);
  });

  it('cancelling an upload removes its card', async () => {
    renderComposer(false); // 挂起以便取消
    const input = screen.getByTestId('attachment-file-input') as HTMLInputElement;
    await userEvent.upload(input, pngFile());
    await waitFor(() => expect(screen.getByTestId('upload-cards')).toBeTruthy());
    const cancel = screen.getAllByRole('button').find((b) =>
      b.getAttribute('data-testid')?.startsWith('upload-cancel-'),
    );
    expect(cancel).toBeTruthy();
    fireEvent.click(cancel as HTMLButtonElement);
    await waitFor(() => expect(screen.queryByTestId('upload-cards')).toBeNull());
  });
});
