/**
 * 附件 API 契约层测试(attachment.md §3):路径 / 方法 / 包络解包 / 频道名,
 * 以及字节直传工具 putBytesWithProgress(XHR)与 sha256Hex(crypto.subtle)。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import {
  SHA256_MAX_BYTES,
  abortUpload,
  attachmentChannel,
  attachmentEventEntity,
  completeMultipart,
  completeUpload,
  deleteAttachment,
  getAttachment,
  getDownloadUrl,
  getThumbnailUrl,
  listCommentAttachments,
  listIssueAttachments,
  putBytesWithProgress,
  requestMultipartParts,
  requestUpload,
  sha256Hex,
} from '../api';

let stub: FetchStub;
let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  stub = stubFetch(fakeResponse({ body: { data: {} } }));
  vi.stubGlobal('fetch', stub.fetchImpl);
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

describe('channel helpers', () => {
  it('builds the issue-scoped channel and parses event entities', () => {
    expect(attachmentChannel('iss-1')).toBe('issue:iss-1');
    expect(attachmentEventEntity('attachment.processed')).toBe('attachment');
    expect(attachmentEventEntity('plainword')).toBe('');
  });
});

describe('endpoint surface', () => {
  it('requests an upload with the link_to payload', async () => {
    await requestUpload(client, {
      file_name: 'a.png',
      file_size: 10,
      mime_type: 'image/png',
      content_hash: 'abc',
      link_to: { type: 'issue', id: 'iss-1', display: 'inline' },
    });
    expect(stub.calls[0].url).toBe('http://api/api/v1/attachments/upload-requests');
    expect(stub.calls[0].init?.method).toBe('POST');
    const body = JSON.parse(String(stub.calls[0].init?.body));
    expect(body.link_to).toEqual({ type: 'issue', id: 'iss-1', display: 'inline' });
    expect(body.content_hash).toBe('abc');
  });

  it('completes and aborts uploads', async () => {
    await completeUpload(client, 'att-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/attachments/att-1/complete');
    expect(stub.calls[0].init?.method).toBe('POST');
    await abortUpload(client, 'att-1');
    expect(stub.calls[1].url).toBe('http://api/api/v1/attachments/att-1/abort');
  });

  it('gets and deletes a single attachment', async () => {
    await getAttachment(client, 'att-1');
    expect(stub.calls[0].init?.method).toBe('GET');
    await deleteAttachment(client, 'att-1');
    expect(stub.calls[1].init?.method).toBe('DELETE');
    expect(stub.calls[1].url).toBe('http://api/api/v1/attachments/att-1');
  });

  it('resolves download and thumbnail descriptors', async () => {
    await getDownloadUrl(client, 'att-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/attachments/att-1/download');
    await getThumbnailUrl(client, 'att-1', 'lg');
    expect(stub.calls[1].url).toContain('/api/v1/attachments/att-1/thumbnail');
    expect(stub.calls[1].url).toContain('size=lg');
    await getThumbnailUrl(client, 'att-1');
    expect(stub.calls[2].url).toContain('size=md');
  });

  it('lists issue and comment attachments with the cursor contract', async () => {
    const listStub = stubFetch(fakeResponse({ body: { data: [], next_cursor: 'cur' } }));
    vi.stubGlobal('fetch', listStub.fetchImpl);
    const issuePage = await listIssueAttachments(client, 'iss-1', { limit: 5, cursor: 'c' });
    expect(listStub.calls[0].url).toContain('/api/v1/issues/iss-1/attachments');
    expect(listStub.calls[0].url).toContain('limit=5');
    expect(issuePage.nextCursor).toBe('cur');
    const commentPage = await listCommentAttachments(client, 'com-1');
    expect(listStub.calls[1].url).toContain('/api/v1/comments/com-1/attachments');
    expect(commentPage.data).toEqual([]);
  });

  it('requests further multipart parts and completes the upload', async () => {
    await requestMultipartParts(client, 'up-1', [3, 4]);
    expect(stub.calls[0].url).toBe('http://api/api/v1/multipart/up-1/parts');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({ part_numbers: [3, 4] });
    await completeMultipart(client, 'up-1', [{ part_number: 1, etag: 'e1' }]);
    expect(stub.calls[1].url).toBe('http://api/api/v1/multipart/up-1/complete');
    expect(JSON.parse(String(stub.calls[1].init?.body))).toEqual({
      parts: [{ part_number: 1, etag: 'e1' }],
    });
  });
});

/* ---- putBytesWithProgress(XMLHttpRequest) ---- */

interface MockXHRInstance {
  upload: { onprogress: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null };
  onload: (() => void) | null;
  onerror: (() => void) | null;
  onabort: (() => void) | null;
  status: number;
  sent: Blob | null;
  abortCalled: boolean;
  open: (method: string, url: string) => void;
  setRequestHeader: (name: string, value: string) => void;
  getResponseHeader: (name: string) => string | null;
  send: (body: Blob) => void;
  abort: () => void;
}

/**
 * 真实浏览器语义的 XHR 桩(M1 回归的关键):
 * - abort() 作用于**未 send** 的请求时不触发任何事件(旧桩无条件 fire onabort,
 *   把「预中止 promise 永不 settle」的真实 bug 盖住了);
 * - 已 settle(成功/失败/中止)后再 abort() 不再重复 fire。
 */
function installXhrMock(): { instances: MockXHRInstance[] } {
  const instances: MockXHRInstance[] = [];
  class MockXHR {
    upload: { onprogress: ((e: unknown) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    status = 200;
    method = '';
    url = '';
    headers: Record<string, string> = {};
    responseHeaders: Record<string, string> = {};
    sent: Blob | null = null;
    settled = false;
    abortCalled = false;
    constructor() {
      instances.push(this as unknown as MockXHRInstance);
    }
    open(method: string, url: string) {
      this.method = method;
      this.url = url;
    }
    setRequestHeader(name: string, value: string) {
      this.headers[name] = value;
    }
    getResponseHeader(name: string) {
      return this.responseHeaders[name] ?? null;
    }
    send(body: Blob) {
      this.sent = body;
    }
    abort() {
      this.abortCalled = true;
      if (this.sent === null || this.settled) return;
      this.settled = true;
      this.onabort?.();
    }
  }
  vi.stubGlobal('XMLHttpRequest', MockXHR);
  return { instances };
}

describe('putBytesWithProgress', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('resolves with the ETag header on success and reports progress', async () => {
    const { instances } = installXhrMock();
    const progress: Array<[number, number]> = [];
    const promise = putBytesWithProgress(
      'http://storage/put',
      new Blob(['x']),
      { 'Content-Type': 'image/png' },
      (loaded, total) => progress.push([loaded, total]),
    );
    const xhr = instances[0] as unknown as {
      headers: Record<string, string>;
      method: string;
      responseHeaders: Record<string, string>;
    } & MockXHRInstance;
    expect(xhr.method).toBe('PUT');
    expect(xhr.headers['Content-Type']).toBe('image/png');
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
    xhr.responseHeaders.ETag = '"etag-1"';
    xhr.status = 200;
    xhr.onload?.();
    await expect(promise).resolves.toEqual({ etag: '"etag-1"' });
    expect(progress).toEqual([[5, 10]]);
  });

  it('rejects on a non-2xx status and on network error', async () => {
    const { instances } = installXhrMock();
    const failing = putBytesWithProgress('http://storage/put', new Blob(['x']), {});
    instances[0].status = 500;
    instances[0].onload?.();
    await expect(failing).rejects.toThrow('status 500');

    const network = putBytesWithProgress('http://storage/put', new Blob(['x']), {});
    instances[1].onerror?.();
    await expect(network).rejects.toThrow('network error');
  });

  it('rejects promptly with AbortError for a PRE-ABORTED signal without relying on XHR events (M1)', async () => {
    const { instances } = installXhrMock();
    const preAborted = new AbortController();
    preAborted.abort();
    const pre = putBytesWithProgress('http://s', new Blob(['x']), {}, undefined, preAborted.signal);
    // 若实现把预中止交给 xhr.abort() 收敛,真实浏览器(与本桩)对未 send 的
    // XHR 不 fire 任何事件,promise 永不 settle,本断言将挂到超时——这正是 M1 回归点。
    await expect(pre).rejects.toMatchObject({ name: 'AbortError' });
    expect(instances).toHaveLength(0); // 直接 reject,连 XHR 都不必构造
  });

  it('aborts a mid-flight upload via the signal listener', async () => {
    const { instances } = installXhrMock();
    const controller = new AbortController();
    const mid = putBytesWithProgress('http://s', new Blob(['x']), {}, undefined, controller.signal);
    expect(instances).toHaveLength(1);
    controller.abort();
    expect(instances[0].abortCalled).toBe(true);
    await expect(mid).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('removes the abort listener once the XHR settles (no dangling signal reference)', async () => {
    const { instances } = installXhrMock();
    const controller = new AbortController();
    const removeSpy = vi.spyOn(controller.signal, 'removeEventListener');
    const promise = putBytesWithProgress('http://s', new Blob(['x']), {}, undefined, controller.signal);
    instances[0].status = 200;
    instances[0].onload?.();
    await expect(promise).resolves.toEqual({ etag: null });
    expect(removeSpy).toHaveBeenCalledWith('abort', expect.any(Function));
  });
});

describe('sha256Hex', () => {
  it('returns lowercase hex for a small file', async () => {
    const digest = vi.fn(async () => new Uint8Array([0, 1, 255]).buffer);
    vi.stubGlobal('crypto', { subtle: { digest }, randomUUID: () => 'id' });
    // jsdom 的 Blob 未实现 arrayBuffer;以最小假 blob 驱动纯逻辑。
    const small = { size: 5, arrayBuffer: async () => new ArrayBuffer(5) } as Blob;
    const hex = await sha256Hex(small);
    expect(hex).toBe('0001ff');
    expect(digest).toHaveBeenCalledWith('SHA-256', expect.any(ArrayBuffer));
  });

  it('skips hashing for files larger than the threshold', async () => {
    const digest = vi.fn();
    vi.stubGlobal('crypto', { subtle: { digest }, randomUUID: () => 'id' });
    const big = { size: SHA256_MAX_BYTES + 1, arrayBuffer: async () => new ArrayBuffer(0) } as Blob;
    await expect(sha256Hex(big)).resolves.toBeNull();
    expect(digest).not.toHaveBeenCalled();
  });
});
