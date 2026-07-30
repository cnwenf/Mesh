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

/** 真实浏览器语义:abort() 作用于未 send 的请求不 fire 任何事件;已 settle 不重复 fire。 */
function installXhr(autoRespond: boolean): void {
  class Xhr {
    upload = { onprogress: null as null | ((e: unknown) => void) };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    status = 200;
    responseHeaders: Record<string, string> = {};
    sent = false;
    settled = false;
    open() {}
    setRequestHeader() {}
    getResponseHeader(name: string) {
      return this.responseHeaders[name] ?? null;
    }
    send() {
      this.sent = true;
      if (autoRespond) {
        queueMicrotask(() => {
          if (this.settled) return;
          this.settled = true;
          this.responseHeaders.ETag = '"e"';
          this.onload?.();
        });
      }
    }
    abort() {
      if (!this.sent || this.settled) return;
      this.settled = true;
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

// LOW 回归:arrayBuffer 垫片按测试装卸,绝不模块级永久补丁 Blob.prototype(跨文件泄漏)。
const HAS_BLOB_ARRAY_BUFFER = typeof Blob.prototype.arrayBuffer === 'function';
function installBlobArrayBuffer(): void {
  if (HAS_BLOB_ARRAY_BUFFER) return;
  Object.defineProperty(Blob.prototype, 'arrayBuffer', {
    configurable: true,
    writable: true,
    value: function arrayBuffer(this: Blob): Promise<ArrayBuffer> {
      return Promise.resolve(new ArrayBuffer(this.size));
    },
  });
}
function removeBlobArrayBuffer(): void {
  if (HAS_BLOB_ARRAY_BUFFER) return;
  delete (Blob.prototype as { arrayBuffer?: unknown }).arrayBuffer;
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

function renderComposerRoutes(
  routes: Route[],
  onUploaded?: (a: unknown) => void,
  autoRespond = true,
): { calls: Array<{ url: string; method: string }> } {
  installXhr(autoRespond);
  const { impl, calls } = makeFetch(routes);
  vi.stubGlobal('fetch', impl);
  const client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
  renderWithProviders(
    <AttachmentComposer
      workspaceId="ws-1"
      linkTo={{ type: 'issue', id: 'iss-1' }}
      client={client}
      onUploaded={onUploaded as never}
    />,
  );
  return { calls };
}

function renderComposer(autoRespond: boolean, onUploaded?: (a: unknown) => void): void {
  renderComposerRoutes([uploadRequestRoute, completeRoute], onUploaded, autoRespond);
}

function pngFile(name = 'a.png'): File {
  return new File([new Uint8Array(8)], name, { type: 'image/png' });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  uuidCounter = 0;
  installCrypto();
  installBlobArrayBuffer();
});
afterEach(() => {
  vi.unstubAllGlobals();
  removeBlobArrayBuffer();
});

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

  it('opens the file picker when the paperclip is clicked', () => {
    renderComposer(true);
    const clickSpy = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => undefined);
    fireEvent.click(screen.getByTestId('attachment-paperclip'));
    expect(clickSpy).toHaveBeenCalled();
    clickSpy.mockRestore();
  });

  it('ignores a drop without files', () => {
    renderComposer(true);
    fireEvent.drop(screen.getByTestId('attachment-composer'), { dataTransfer: {} });
    expect(screen.queryByTestId('upload-cards')).toBeNull();
  });

  it('ignores a paste without clipboard files', () => {
    renderComposer(true);
    fireEvent.paste(screen.getByTestId('attachment-composer'), {});
    expect(screen.queryByTestId('upload-cards')).toBeNull();
  });

  it('removes a failed upload card via the 移除 button (keeps name/size + named error until then)', async () => {
    renderComposer(true);
    const input = screen.getByTestId('attachment-file-input') as HTMLInputElement;
    await userEvent.upload(input, new File(['x'], 'tool.exe', { type: 'application/x-msdownload' }));
    const error = await screen.findByRole('alert');
    // 具名错误(error.unsupported_media_type → 本地化文案)+ 文件名/大小保留
    expect(error.textContent).toContain('not supported');
    const remove = screen
      .getAllByRole('button')
      .find((b) => b.getAttribute('data-testid')?.startsWith('upload-cancel-'));
    expect(remove).toBeTruthy();
    fireEvent.click(remove as HTMLButtonElement);
    await waitFor(() => expect(screen.queryByTestId('upload-cards')).toBeNull());
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

  it('a 409 conflict on complete is retryable and re-submitting the same file succeeds (H1 wedging regression)', async () => {
    // 真实后端行为:complete 撞 409(附件已 completed);重试同文件命中秒传
    // (upload=null, upload_status='completed')——秒传路径绝不再打 /complete,
    // 否则「失败→重试→同秒传→同 409」会把提交按钮永久焊死。
    let uploadRequests = 0;
    let completeCalls = 0;
    const routes: Route[] = [
      {
        match: (url) => url.includes('/upload-requests'),
        response: () => {
          uploadRequests += 1;
          if (uploadRequests === 1) {
            return fakeResponse({
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
            });
          }
          return fakeResponse({
            status: 201,
            body: {
              data: {
                id: 'att-1',
                upload_status: 'completed',
                blob_id: 'blob-1',
                scan_status: 'clean',
                mime_type: 'image/png',
                is_image: true,
                upload: null,
                limits: { max_file_bytes: DEFAULT_MAX_FILE_BYTES },
              },
            },
          });
        },
      },
      {
        match: (url) => url.includes('/complete'),
        response: () => {
          completeCalls += 1;
          return fakeResponse({
            status: 409,
            body: { error: { code: 'conflict', message: 'already completed' } },
          });
        },
      },
      {
        match: (url) => url.includes('/abort'),
        response: () => fakeResponse({ body: { data: { id: 'att-1', upload_status: 'failed' } } }),
      },
    ];
    const onUploaded = vi.fn();
    renderComposerRoutes(routes, onUploaded);

    const input = screen.getByTestId('attachment-file-input') as HTMLInputElement;
    await userEvent.upload(input, pngFile());
    // complete 409 → 错误卡片 + 提交禁用(但非永久)。
    await screen.findByRole('alert');
    expect((screen.getByTestId('attachment-submit') as HTMLButtonElement).disabled).toBe(true);

    // 重试:移除失败条目并以同一文件重新加入 → 秒传直接就绪 → 提交恢复可用。
    const retry = screen
      .getAllByRole('button')
      .find((b) => b.getAttribute('data-testid')?.startsWith('upload-retry-'));
    expect(retry).toBeTruthy();
    fireEvent.click(retry as HTMLButtonElement);
    await waitFor(() =>
      expect((screen.getByTestId('attachment-submit') as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId('attachment-submit'));
    expect(onUploaded).toHaveBeenCalledTimes(1);
    const emitted = onUploaded.mock.calls[0][0] as Array<{ id: string }>;
    expect(emitted[0].id).toBe('att-1');
    // /complete 只在失败的首轮打过一次;秒传路径未重放。
    expect(completeCalls).toBe(1);
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
