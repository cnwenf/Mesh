/**
 * MessageAttachments 测试(chat-session.md §4.2 / attachment.md §3 扫描闸门):
 * pending/scanning → 扫描中占位(无下载);infected → 拦截(无下载);
 * clean/skipped + client → 图片走缩略图 + 灯箱、非图片走文件卡 + 签名下载;
 * clean + 无 client → 名称 + 大小退化卡;getAttachment 失败 → 退化卡;下载失败 → toast。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { MessageAttachments } from '../MessageAttachments';
import type { ChatAttachmentRef } from '../types';

function ref(overrides: Partial<ChatAttachmentRef> = {}): ChatAttachmentRef {
  return {
    id: 'a-1',
    file_name: 'file.bin',
    mime_type: 'application/octet-stream',
    byte_size: 2048,
    scan_status: 'clean',
    ...overrides,
  };
}

function fullAttachment(id: string, isImage: boolean) {
  return {
    id,
    blob_id: 'b-1',
    file_name: isImage ? 'p.png' : 'r.pdf',
    file_size: 2048,
    mime_type: isImage ? 'image/png' : 'application/pdf',
    extension: isImage ? 'png' : 'pdf',
    is_image: isImage,
    image_width: isImage ? 100 : null,
    image_height: isImage ? 80 : null,
    scan_status: 'clean',
    upload_status: 'completed',
    uploader: null,
    links: [],
    thumbnail_url: isImage ? `/api/v1/attachments/${id}/thumbnail` : null,
    download_url: `/api/v1/attachments/${id}/download`,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
  };
}

interface RouterOptions {
  readonly isImage?: boolean;
  readonly detailFails?: boolean;
  readonly downloadFails?: boolean;
  /** 自定义下载签名 URL(用于协议/合法性闸门分支)。 */
  readonly downloadUrl?: string;
}

function routedClient(opts: RouterOptions, calls: string[]): MeshApiClient {
  const fetchImpl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.includes('/thumbnail'))
      return fakeResponse({
        body: { data: { url: 'http://x/thumb.png', size: 'md', expires_at: 'x' } },
      });
    if (url.includes('/download')) {
      if (opts.downloadFails)
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      return fakeResponse({
        body: {
          data: { url: opts.downloadUrl ?? 'http://x/dl', file_name: 'p.png', expires_at: 'x' },
        },
      });
    }
    // 附件详情(GET /attachments/{id})
    if (opts.detailFails)
      return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
    return fakeResponse({ body: { data: fullAttachment('a-1', opts.isImage ?? false) } });
  }) as typeof fetch;
  return new MeshApiClient({ baseUrl: 'http://api', getToken: () => null, fetchImpl });
}

describe('MessageAttachments(§4.2 扫描闸门)', () => {
  it('pending 呈现扫描中占位且无下载', () => {
    renderWithProviders(
      <MessageAttachments attachments={[ref({ scan_status: 'pending' })]} messageId="m-1" />,
    );
    expect(screen.getByTestId('chat-attachment-scanning-a-1')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-attachment-download-a-1')).toBeNull();
  });

  it('scanning 呈现扫描中占位', () => {
    renderWithProviders(
      <MessageAttachments attachments={[ref({ scan_status: 'scanning' })]} messageId="m-1" />,
    );
    expect(screen.getByTestId('chat-attachment-scanning-a-1')).toBeInTheDocument();
  });

  it('infected 呈现拦截态且无下载', () => {
    renderWithProviders(
      <MessageAttachments attachments={[ref({ scan_status: 'infected' })]} messageId="m-1" />,
    );
    expect(screen.getByTestId('chat-attachment-blocked-a-1')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-attachment-download-a-1')).toBeNull();
  });

  it('clean + 无 client → 名称 + 大小退化卡', () => {
    renderWithProviders(
      <MessageAttachments
        attachments={[ref({ file_name: 'r.pdf', byte_size: 2048 })]}
        messageId="m-1"
      />,
    );
    const card = screen.getByTestId('chat-attachment-file-a-1');
    expect(card).toHaveTextContent('r.pdf');
    expect(card).toHaveTextContent('2.0 KB');
  });

  it('skipped 放行(同 clean)', () => {
    renderWithProviders(
      <MessageAttachments
        attachments={[ref({ scan_status: 'skipped', file_name: 's.txt' })]}
        messageId="m-1"
      />,
    );
    expect(screen.getByTestId('chat-attachment-file-a-1')).toHaveTextContent('s.txt');
  });

  it('非图片放行项:文件卡 + 签名下载', async () => {
    const calls: string[] = [];
    const client = routedClient({ isImage: false }, calls);
    renderWithProviders(
      <MessageAttachments
        attachments={[ref({ file_name: 'r.pdf' })]}
        messageId="m-1"
        client={client}
      />,
    );
    const card = await screen.findByTestId('chat-attachment-file-a-1');
    expect(card).toHaveTextContent('r.pdf');
    fireEvent.click(screen.getByTestId('chat-attachment-download-a-1'));
    await waitFor(() => expect(calls.some((u) => u.includes('/download'))).toBe(true));
  });

  it('图片放行项:缩略图 + 灯箱 + 下载', async () => {
    const calls: string[] = [];
    const client = routedClient({ isImage: true }, calls);
    renderWithProviders(
      <MessageAttachments
        attachments={[ref({ id: 'a-1', file_name: 'p.png', mime_type: 'image/png' })]}
        messageId="m-1"
        client={client}
      />,
    );
    expect(await screen.findByTestId('chat-attachment-image-a-1')).toBeInTheDocument();
    // 缩略图解析签名 URL 后呈现(Thumbnail)
    expect(await screen.findByTestId('attachment-thumb-a-1')).toBeInTheDocument();
    // 点击缩略图打开灯箱(解析原图下载 URL)
    fireEvent.click(screen.getByTestId('attachment-thumb-a-1'));
    expect(await screen.findByTestId('lightbox-image')).toBeInTheDocument();
  });

  it('getAttachment 失败 → 退化卡(名称 + 大小)', async () => {
    const calls: string[] = [];
    const client = routedClient({ detailFails: true }, calls);
    renderWithProviders(
      <MessageAttachments
        attachments={[ref({ file_name: 'r.pdf' })]}
        messageId="m-1"
        client={client}
      />,
    );
    expect(await screen.findByTestId('chat-attachment-file-a-1')).toHaveTextContent('r.pdf');
  });

  it('下载失败 → toast', async () => {
    const calls: string[] = [];
    const client = routedClient({ isImage: false, downloadFails: true }, calls);
    renderWithProviders(
      <MessageAttachments
        attachments={[ref({ file_name: 'r.pdf' })]}
        messageId="m-1"
        client={client}
      />,
    );
    await screen.findByTestId('chat-attachment-file-a-1');
    // 下载按钮在签名 URL 异步解析后出现(高负载下非同步渲染)。
    fireEvent.click(await screen.findByTestId('chat-attachment-download-a-1'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
  });

  it('图片灯箱:下载 / 定位关闭 / 关闭按钮', async () => {
    const calls: string[] = [];
    const client = routedClient({ isImage: true }, calls);
    renderWithProviders(
      <MessageAttachments
        attachments={[ref({ mime_type: 'image/png' })]}
        messageId="m-1"
        client={client}
      />,
    );
    fireEvent.click(await screen.findByTestId('attachment-thumb-a-1'));
    expect(await screen.findByTestId('lightbox-image')).toBeInTheDocument();
    // 灯箱内下载
    fireEvent.click(screen.getByTestId('lightbox-download'));
    await waitFor(() =>
      expect(calls.filter((u) => u.includes('/download')).length).toBeGreaterThanOrEqual(2),
    );
    // 定位 → 关闭灯箱
    fireEvent.click(screen.getByTestId('lightbox-locate'));
    await waitFor(() => expect(screen.queryByTestId('lightbox-image')).toBeNull());
    // 重新打开后经关闭按钮(onClose)关闭
    fireEvent.click(screen.getByTestId('attachment-thumb-a-1'));
    expect(await screen.findByTestId('lightbox-image')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByTestId('lightbox-image')).toBeNull());
  });

  it('下载签名 URL 为非 http(s) 协议 → 闸门拦截不触发下载', async () => {
    const calls: string[] = [];
    const client = routedClient({ isImage: false, downloadUrl: 'javascript:alert(1)' }, calls);
    renderWithProviders(
      <MessageAttachments
        attachments={[ref({ file_name: 'r.pdf' })]}
        messageId="m-1"
        client={client}
      />,
    );
    await screen.findByTestId('chat-attachment-file-a-1');
    // 下载按钮在签名 URL 异步解析后出现(高负载下非同步渲染)。
    fireEvent.click(await screen.findByTestId('chat-attachment-download-a-1'));
    // 端点被调用,但非法协议被拦截(无 toast、无崩溃)
    await waitFor(() => expect(calls.some((u) => u.includes('/download'))).toBe(true));
    expect(document.querySelector('.mesh-toast')).toBeNull();
  });

  it('下载签名 URL 非法(解析抛错)→ 静默返回不崩溃', async () => {
    const calls: string[] = [];
    const client = routedClient({ isImage: false, downloadUrl: '::not a url::' }, calls);
    renderWithProviders(
      <MessageAttachments
        attachments={[ref({ file_name: 'r.pdf' })]}
        messageId="m-1"
        client={client}
      />,
    );
    await screen.findByTestId('chat-attachment-file-a-1');
    fireEvent.click(screen.getByTestId('chat-attachment-download-a-1'));
    await waitFor(() => expect(calls.some((u) => u.includes('/download'))).toBe(true));
    expect(document.querySelector('.mesh-toast')).toBeNull();
  });
});
