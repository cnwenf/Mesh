/**
 * useAttachmentUploader 流水线测试(attachment.md §3.1–§3.3 / §4.2):
 * 秒传 / 单段 PUT 进度→complete→ready / scanning 态 / 分块 / 取消(abort)/ 错误映射 / 预校验。
 * XHR 与 crypto.subtle 以桩驱动;fetch 经 MeshApiClient(注入)走 stubFetch。
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import {
  ALLOWED_MIME_TYPES,
  DEFAULT_MAX_FILE_BYTES,
  DEFAULT_MAX_IMAGE_BYTES,
  useAttachmentUploader,
  validateFile,
} from '../useAttachmentUploader';
import type { Attachment } from '../types';

/* ---- 桩基础设施 ---- */

interface MockXHR {
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null };
  onload: (() => void) | null;
  onerror: (() => void) | null;
  onabort: (() => void) | null;
  status: number;
  responseHeaders: Record<string, string>;
  method: string;
  url: string;
  headers: Record<string, string>;
  sent: boolean;
  abortCalled: boolean;
  open: (m: string, u: string) => void;
  setRequestHeader: (n: string, v: string) => void;
  getResponseHeader: (n: string) => string | null;
  send: (b: Blob) => void;
  abort: () => void;
}

/**
 * 真实浏览器语义的 XHR 桩:abort() 作用于未 send 的请求不 fire 任何事件,
 * 已 settle 后不重复 fire(旧实现无条件 fire onabort,会掩盖 M1 悬挂 bug)。
 */
function installXhr(autoRespond: boolean): MockXHR[] {
  const instances: MockXHR[] = [];
  class Xhr {
    upload: { onprogress: ((e: unknown) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    status = 200;
    responseHeaders: Record<string, string> = {};
    method = '';
    url = '';
    headers: Record<string, string> = {};
    sent = false;
    settled = false;
    abortCalled = false;
    constructor() {
      instances.push(this as unknown as MockXHR);
    }
    open(m: string, u: string) {
      this.method = m;
      this.url = u;
    }
    setRequestHeader(n: string, v: string) {
      this.headers[n] = v;
    }
    getResponseHeader(n: string) {
      return this.responseHeaders[n] ?? null;
    }
    send() {
      this.sent = true;
      if (autoRespond) {
        queueMicrotask(() => {
          if (this.settled) return;
          this.settled = true;
          this.responseHeaders.ETag = '"etag"';
          this.status = 200;
          this.onload?.();
        });
      }
    }
    abort() {
      this.abortCalled = true;
      if (!this.sent || this.settled) return;
      this.settled = true;
      this.onabort?.();
    }
  }
  vi.stubGlobal('XMLHttpRequest', Xhr);
  return instances;
}

// jsdom 的 Blob/File 可能未实现 arrayBuffer;补最小垫片以驱动 sha256Hex 纯逻辑。
// LOW 回归:绝不模块级永久补丁(Blob.prototype 跨测试文件共享,泄漏会污染
// 其他套件)——beforeEach 按需装上、afterEach 必拆除。
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

let uuidCounter = 0;
function installCrypto(): void {
  vi.stubGlobal('crypto', {
    subtle: { digest: vi.fn(async () => new Uint8Array([1, 2, 3]).buffer) },
    randomUUID: () => `uuid-${uuidCounter++}`,
  });
}

let stub: FetchStub;
let client: MeshApiClient;

function pngFile(name = 'a.png', size = 12): File {
  return new File([new Uint8Array(size)], name, { type: 'image/png' });
}

function uploadRequestResponse(upload: unknown, id = 'att-1') {
  return fakeResponse({
    status: 201,
    body: {
      data: {
        id,
        upload_status: 'pending',
        blob_id: null,
        scan_status: 'pending',
        mime_type: 'image/png',
        is_image: true,
        upload,
        limits: { max_file_bytes: DEFAULT_MAX_FILE_BYTES },
      },
    },
  });
}

/**
 * 秒传响应 —— 按后端**真实形态** mock(与 service.py 状态机逐字对齐):
 * 内容命中已可读 blob → upload_status='completed'、upload=null、附件行 + links
 * 已落库;前端对 completed 再 complete 必 409。旧测试把秒传 mock 成
 * pending + complete 成功,与自家后端行为相反,故必须按此形态回归。
 */
function instantUploadResponse(scanStatus: 'clean' | 'pending', id = 'att-1') {
  return fakeResponse({
    status: 201,
    body: {
      data: {
        id,
        upload_status: 'completed',
        blob_id: 'blob-1',
        scan_status: scanStatus,
        mime_type: 'image/png',
        is_image: true,
        upload: null,
        limits: { max_file_bytes: DEFAULT_MAX_FILE_BYTES },
      },
    },
  });
}

function completeResponse(scanStatus: Attachment['scan_status'], id = 'att-1') {
  return fakeResponse({
    body: {
      data: {
        id,
        blob_id: 'blob-1',
        file_name: 'a.png',
        file_size: 12,
        mime_type: 'image/png',
        extension: 'png',
        is_image: true,
        image_width: 10,
        image_height: 10,
        scan_status: scanStatus,
        upload_status: 'completed',
        uploader: null,
        links: [],
        thumbnail_url: null,
        download_url: `/api/v1/attachments/${id}/download`,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    },
  });
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

describe('validateFile (client pre-validation)', () => {
  it('accepts allowlisted mime types and exposes the allowlist', () => {
    expect(validateFile(pngFile())).toBeNull();
    expect(ALLOWED_MIME_TYPES).toContain('image/png');
    expect(ALLOWED_MIME_TYPES).toContain('application/pdf');
  });

  it('rejects unsupported types (mime + extension fallback)', () => {
    const exe = { size: 10, type: 'application/x-msdownload', name: 'tool.exe' } as File;
    expect(validateFile(exe)).toBe('error.unsupported_media_type');
    const noMime = { size: 10, type: '', name: 'tool.exe' } as File;
    expect(validateFile(noMime)).toBe('error.unsupported_media_type');
    const extFallback = { size: 10, type: '', name: 'notes.md' } as File;
    expect(validateFile(extFallback)).toBeNull();
  });

  it('enforces size limits (25MB images / 100MB files)', () => {
    const bigImage = { size: DEFAULT_MAX_IMAGE_BYTES + 1, type: 'image/png', name: 'x.png' } as File;
    expect(validateFile(bigImage)).toBe('error.file_too_large');
    const bigFile = { size: DEFAULT_MAX_FILE_BYTES + 1, type: 'application/pdf', name: 'x.pdf' } as File;
    expect(validateFile(bigFile)).toBe('error.file_too_large');
    const okFile = { size: DEFAULT_MAX_FILE_BYTES, type: 'application/pdf', name: 'x.pdf' } as File;
    expect(validateFile(okFile)).toBeNull();
  });
});

describe('pipeline', () => {
  it('instant upload (upload=null, completed) goes straight to ready WITHOUT calling /complete (H1)', async () => {
    installXhr(true);
    stub = stubFetch(instantUploadResponse('clean'));
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));

    act(() => result.current.addFiles([pngFile()], { type: 'issue', id: 'iss-1' }));
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('ready'));
    expect(result.current.uploads[0].attachment?.scan_status).toBe('clean');
    expect(result.current.uploads[0].attachment?.id).toBe('att-1');
    // 服务端已 completed:全程只有 upload-requests 一次调用,绝不打 /complete(重放必 409)。
    expect(stub.calls.map((c) => c.url)).toEqual([
      'http://api/api/v1/attachments/upload-requests',
    ]);
    expect(stub.calls.some((c) => c.url.includes('/complete'))).toBe(false);
  });

  it('single PUT reports progress then completes to ready', async () => {
    const xhrs = installXhr(true);
    stub = stubFetch(
      uploadRequestResponse({
        method: 'PUT',
        url: 'http://storage/put',
        headers: { 'Content-Type': 'image/png' },
        expires_at: '2026-01-01T01:00:00Z',
      }),
      completeResponse('clean'),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));

    act(() => result.current.addFiles([pngFile()]));
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('ready'));
    expect(xhrs[0].url).toBe('http://storage/put');
    expect(xhrs[0].method).toBe('PUT');
    expect(result.current.uploads[0].progress).toBe(1);
  });

  it('instant upload with a pending scan lands in scanning WITHOUT calling /complete (H1)', async () => {
    installXhr(true);
    stub = stubFetch(instantUploadResponse('pending'));
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));

    act(() => result.current.addFiles([pngFile()]));
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('scanning'));
    expect(result.current.uploads[0].attachment?.scan_status).toBe('pending');
    expect(stub.calls.some((c) => c.url.includes('/complete'))).toBe(false);
  });

  it('lands in scanning when the scan is still pending after a normal complete', async () => {
    installXhr(true);
    stub = stubFetch(
      uploadRequestResponse({
        method: 'PUT',
        url: 'http://storage/put',
        headers: {},
        expires_at: '2026-01-01T01:00:00Z',
      }),
      completeResponse('pending'),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));

    act(() => result.current.addFiles([pngFile()]));
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('scanning'));
    expect(result.current.uploads[0].attachment?.scan_status).toBe('pending');
  });

  it('surfaces a 409 conflict from /complete as an error entry without wedging later uploads (H1 regression)', async () => {
    installXhr(true);
    stub = stubFetch(
      // 第一次:普通单段直传,complete 撞 409(对 completed 附件重放 complete 的真实后端行为)。
      uploadRequestResponse({
        method: 'PUT',
        url: 'http://storage/put',
        headers: {},
        expires_at: '2026-01-01T01:00:00Z',
      }),
      fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'already completed' } } }),
      // 第二次:同文件重试命中秒传(服务端此前已建好附件),直接 completed。
      instantUploadResponse('clean'),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));

    act(() => result.current.addFiles([pngFile()]));
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('error'));
    expect(result.current.uploads[0].errorKey).toBe('error.conflict');

    // 失败条目不得焊死流水线:同文件重新加入走秒传,直接就绪。
    act(() => result.current.addFiles([pngFile()]));
    await waitFor(() => expect(result.current.uploads[1].phase).toBe('ready'));
    expect(result.current.uploads[1].attachment?.id).toBe('att-1');
    // /complete 只被调用过一次(失败那次);秒传路径未再重放。
    expect(stub.calls.filter((c) => c.url.includes('/complete'))).toHaveLength(1);
  });

  it('aborts in-flight XHRs and attempts a server-side abort on unmount (M2)', async () => {
    const xhrs = installXhr(false); // XHR 挂起,保持上传中
    stub = stubFetch(
      uploadRequestResponse({
        method: 'PUT',
        url: 'http://storage/put',
        headers: {},
        expires_at: '2026-01-01T01:00:00Z',
      }),
      fakeResponse({ body: { data: { id: 'att-1', upload_status: 'failed' } } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result, unmount } = renderHook(() => useAttachmentUploader({ client }));

    act(() => result.current.addFiles([pngFile()]));
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('uploading'));
    expect(result.current.uploads[0].attachmentId).toBe('att-1');

    unmount();

    // 在途 XHR 经 AbortController 收到 abort()(signal aborted → xhr.abort())。
    expect(xhrs[0].abortCalled).toBe(true);
    // 已拿到 attachmentId 且 pre-complete:尽力通知服务端中止(fire-and-forget)。
    await waitFor(() =>
      expect(stub.calls.some((c) => c.url.includes('/attachments/att-1/abort'))).toBe(true),
    );
  });

  it('multipart upload puts each part, completes, then finalizes', async () => {
    const xhrs = installXhr(true);
    stub = stubFetch(
      uploadRequestResponse({
        upload_id: 'up-1',
        part_urls: [{ part_number: 1, url: 'http://storage/part-1' }],
        part_size: 6,
        part_count: 2,
        expires_at: '2026-01-01T01:00:00Z',
      }),
      fakeResponse({ body: { data: { part_urls: [{ part_number: 2, url: 'http://storage/part-2' }], part_size: 6, part_count: 2 } } }),
      fakeResponse({ body: { data: { upload_id: 'up-1' } } }),
      completeResponse('clean'),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));

    act(() => result.current.addFiles([pngFile('a.png', 10)]));
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('ready'));
    // 两个 part 各一次 PUT
    expect(xhrs.map((x) => x.url).sort()).toEqual(['http://storage/part-1', 'http://storage/part-2']);
    const completeCall = stub.calls.find((c) => c.url.includes('/multipart/up-1/complete'));
    const parts = JSON.parse(String(completeCall?.init?.body)).parts;
    expect(parts).toHaveLength(2);
  });

  it('cancel aborts the in-flight upload and notifies the server', async () => {
    installXhr(false); // XHR 挂起,不自动完成
    stub = stubFetch(
      uploadRequestResponse({
        method: 'PUT',
        url: 'http://storage/put',
        headers: {},
        expires_at: '2026-01-01T01:00:00Z',
      }),
      completeResponse('clean'),
      fakeResponse({ body: { data: { id: 'att-1', upload_status: 'failed' } } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));

    act(() => result.current.addFiles([pngFile()]));
    // 等待 upload-request 落定(attachmentId 已知)且进入上传阶段
    await waitFor(() => expect(result.current.uploads[0].attachmentId).toBe('att-1'));
    const localId = result.current.uploads[0].localId;
    act(() => result.current.cancel(localId));
    await waitFor(() => expect(result.current.uploads).toHaveLength(0));
    await waitFor(() =>
      expect(stub.calls.some((c) => c.url.includes('/attachments/att-1/abort'))).toBe(true),
    );
  });

  it('maps a server error code to an i18n key', async () => {
    installXhr(true);
    stub = stubFetch(
      fakeResponse({ status: 403, body: { error: { code: 'quota_exceeded', message: 'full' } } }),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));

    act(() => result.current.addFiles([pngFile()]));
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('error'));
    expect(result.current.uploads[0].errorKey).toBe('error.quota_exceeded');
  });

  it('fails fast on client pre-validation without hitting the network', async () => {
    installXhr(true);
    stub = stubFetch();
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));

    const exe = new File(['x'], 'tool.exe', { type: 'application/x-msdownload' });
    act(() => result.current.addFiles([exe]));
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('error'));
    expect(result.current.uploads[0].errorKey).toBe('error.unsupported_media_type');
    expect(stub.calls).toHaveLength(0);
  });

  it('addFiles with an empty list is a no-op', () => {
    installXhr(true);
    stub = stubFetch();
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));
    act(() => result.current.addFiles([]));
    expect(result.current.uploads).toHaveLength(0);
  });

  it('falls back to a generated local id when crypto.randomUUID is unavailable', async () => {
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn() } }); // 无 randomUUID
    installXhr(true);
    stub = stubFetch();
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));
    const exe = new File(['x'], 'tool.exe', { type: 'application/x-msdownload' });
    act(() => result.current.addFiles([exe]));
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('error'));
    expect(result.current.uploads[0].localId).toMatch(/^upload-/);
  });

  it('maps single-PUT progress (incl. zero-total) then completes', async () => {
    const xhrs = installXhr(false);
    stub = stubFetch(
      uploadRequestResponse({ method: 'PUT', url: 'http://s', headers: {}, expires_at: 'x' }),
      completeResponse('clean'),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));
    act(() => result.current.addFiles([pngFile()]));
    await waitFor(() => expect(xhrs).toHaveLength(1));
    act(() => xhrs[0].upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 }));
    await waitFor(() => expect(result.current.uploads[0].progress).toBe(0.5));
    // total=0 分支:进度回退 0
    act(() => xhrs[0].upload.onprogress?.({ lengthComputable: true, loaded: 0, total: 0 }));
    await waitFor(() => expect(result.current.uploads[0].progress).toBe(0));
    act(() => {
      xhrs[0].responseHeaders.ETag = '"e"';
      xhrs[0].status = 200;
      xhrs[0].onload?.();
    });
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('ready'));
  });

  it('maps a non-API upload failure to error.network', async () => {
    const xhrs = installXhr(false);
    stub = stubFetch(
      uploadRequestResponse({ method: 'PUT', url: 'http://s', headers: {}, expires_at: 'x' }),
      completeResponse('clean'),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));
    act(() => result.current.addFiles([pngFile()]));
    await waitFor(() => expect(xhrs).toHaveLength(1));
    act(() => xhrs[0].onerror?.());
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('error'));
    expect(result.current.uploads[0].errorKey).toBe('error.network');
  });

  it('reports per-part progress during a multipart upload', async () => {
    const xhrs = installXhr(false);
    stub = stubFetch(
      uploadRequestResponse({
        upload_id: 'up-1',
        part_urls: [{ part_number: 1, url: 'http://s/p1' }],
        part_size: 6,
        part_count: 2,
        expires_at: 'x',
      }),
      fakeResponse({ body: { data: { part_urls: [{ part_number: 2, url: 'http://s/p2' }], part_size: 6, part_count: 2 } } }),
      fakeResponse({ body: { data: { upload_id: 'up-1' } } }),
      completeResponse('clean'),
    );
    vi.stubGlobal('fetch', stub.fetchImpl);
    client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
    const { result } = renderHook(() => useAttachmentUploader({ client }));
    act(() => result.current.addFiles([pngFile('a.png', 10)]));
    await waitFor(() => expect(xhrs).toHaveLength(1));
    act(() => xhrs[0].upload.onprogress?.({ lengthComputable: true, loaded: 6, total: 6 }));
    await waitFor(() => expect(result.current.uploads[0].progress).toBeCloseTo(0.6));
    act(() => {
      xhrs[0].responseHeaders.ETag = '"e1"';
      xhrs[0].status = 200;
      xhrs[0].onload?.();
    });
    await waitFor(() => expect(xhrs).toHaveLength(2));
    act(() => xhrs[1].upload.onprogress?.({ lengthComputable: true, loaded: 4, total: 4 }));
    act(() => {
      xhrs[1].responseHeaders.ETag = '"e2"';
      xhrs[1].status = 200;
      xhrs[1].onload?.();
    });
    await waitFor(() => expect(result.current.uploads[0].phase).toBe('ready'));
    expect(result.current.uploads[0].progress).toBe(1);
  });
});
