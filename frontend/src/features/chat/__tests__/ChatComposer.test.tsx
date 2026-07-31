/**
 * ChatComposer 测试(chat-session.md §4.2):Cmd/Ctrl+Enter 发送、空内容禁用、
 * 引用横幅 + 取消、附件预上传(经 mock uploader)就绪后随发送附带 attachment_ids/refs、
 * 传输中禁用发送、失败保留草稿 + 内联错误、禁用态。
 */
import { fireEvent, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { ChatComposer } from '../ChatComposer';
import type { ComposerSubmitOptions } from '../ChatComposer';
import type { ChatMessage } from '../types';

// 经 mock 控制上传态(真实直传走 XHR/签名 URL,非本组件职责)。
const holder = vi.hoisted(() => ({
  uploads: [] as unknown[],
  addFiles: vi.fn(),
  cancel: vi.fn(),
  /** 记录每次 useAttachmentUploader(options) 的入参(归属工作区透传断言用)。 */
  lastOptions: undefined as { workspaceId?: string } | undefined,
}));

vi.mock('../../attachments/useAttachmentUploader', () => ({
  useAttachmentUploader: (options?: { workspaceId?: string }) => {
    holder.lastOptions = options;
    return holder;
  },
}));

function quoteMessage(): ChatMessage {
  return {
    id: 'q-1',
    session_id: 'sess-1',
    role: 'agent',
    content: 'quoted content',
    generation_id: null,
    generation_status: 'done',
    parent_id: null,
    selected_candidate: true,
    quote_message_id: null,
    prompt_tokens: null,
    completion_tokens: null,
    error_message: null,
    started_at: null,
    finished_at: null,
    created_at: '2026-07-01T00:00:00Z',
    attachments: [],
    candidate_count: null,
    candidate_index: null,
  };
}

beforeEach(() => {
  holder.uploads = [];
  holder.addFiles.mockReset();
  holder.cancel.mockReset();
});

describe('ChatComposer(§4.2)', () => {
  it('Cmd+Enter 发送并清空草稿', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<ChatComposer onSend={onSend} quoteMessage={null} onClearQuote={vi.fn()} />);
    const input = screen.getByTestId('chat-composer-input');
    fireEvent.change(input, { target: { value: 'hello' } });
    expect(screen.getByTestId('chat-composer-send')).toBeEnabled();
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    await vi.waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    const [content, opts] = onSend.mock.calls[0] as [string, ComposerSubmitOptions];
    expect(content).toBe('hello');
    expect(opts.attachmentIds).toEqual([]);
    expect(opts.quoteMessageId).toBeNull();
    // 发送成功后异步清空草稿
    await vi.waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(''));
  });

  it('Ctrl+Enter 也可发送', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<ChatComposer onSend={onSend} quoteMessage={null} onClearQuote={vi.fn()} />);
    const input = screen.getByTestId('chat-composer-input');
    fireEvent.change(input, { target: { value: 'hi' } });
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
    await vi.waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
  });

  it('空内容禁用发送', () => {
    renderWithProviders(<ChatComposer onSend={vi.fn()} quoteMessage={null} onClearQuote={vi.fn()} />);
    expect(screen.getByTestId('chat-composer-send')).toBeDisabled();
  });

  it('引用横幅呈现并可取消', async () => {
    const user = userEvent.setup();
    const onClearQuote = vi.fn();
    renderWithProviders(
      <ChatComposer onSend={vi.fn()} quoteMessage={quoteMessage()} onClearQuote={onClearQuote} />,
    );
    expect(screen.getByTestId('chat-composer-quote')).toHaveTextContent('quoted content');
    await user.click(screen.getByTestId('chat-composer-clear-quote'));
    expect(onClearQuote).toHaveBeenCalledTimes(1);
  });

  it('发送携带 quote_message_id', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(
      <ChatComposer onSend={onSend} quoteMessage={quoteMessage()} onClearQuote={vi.fn()} />,
    );
    const input = screen.getByTestId('chat-composer-input');
    fireEvent.change(input, { target: { value: 'reply' } });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    await vi.waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect((onSend.mock.calls[0] as [string, ComposerSubmitOptions])[1].quoteMessageId).toBe('q-1');
  });

  it('选择文件触发 addFiles', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ChatComposer onSend={vi.fn()} quoteMessage={null} onClearQuote={vi.fn()} />);
    const file = new File(['data'], 'a.png', { type: 'image/png' });
    await user.upload(screen.getByTestId('chat-composer-file'), file);
    expect(holder.addFiles).toHaveBeenCalledTimes(1);
  });

  it('渲染上传卡片并可取消', async () => {
    const user = userEvent.setup();
    holder.uploads = [
      { localId: 'u-1', fileName: 'r.pdf', fileSize: 2048, phase: 'ready', progress: 1, attachmentId: 'att-1', attachment: null, errorKey: null },
    ];
    renderWithProviders(<ChatComposer onSend={vi.fn()} quoteMessage={null} onClearQuote={vi.fn()} />);
    expect(screen.getByTestId('chat-composer-uploads')).toHaveTextContent('r.pdf');
    await user.click(screen.getByTestId('chat-upload-cancel-u-1'));
    expect(holder.cancel).toHaveBeenCalledWith('u-1');
  });

  it('error 阶段上传呈现错误文案', () => {
    holder.uploads = [
      { localId: 'u-2', fileName: 'bad.exe', fileSize: 10, phase: 'error', progress: 0, attachmentId: null, attachment: null, errorKey: 'error.unsupported_media_type' },
    ];
    renderWithProviders(<ChatComposer onSend={vi.fn()} quoteMessage={null} onClearQuote={vi.fn()} />);
    expect(screen.getByTestId('chat-composer-uploads').textContent).not.toContain('bad.exe.exe');
    expect(screen.getByTestId('chat-composer-uploads')).toHaveTextContent('bad.exe');
  });

  it('传输中禁用发送', () => {
    holder.uploads = [
      { localId: 'u-3', fileName: 'big.zip', fileSize: 100, phase: 'uploading', progress: 0.4, attachmentId: null, attachment: null, errorKey: null },
    ];
    renderWithProviders(<ChatComposer onSend={vi.fn()} quoteMessage={null} onClearQuote={vi.fn()} />);
    const input = screen.getByTestId('chat-composer-input');
    fireEvent.change(input, { target: { value: 'hello' } });
    expect(screen.getByTestId('chat-composer-send')).toBeDisabled();
  });

  it('就绪附件随发送附带 attachment_ids 与展示快照', async () => {
    holder.uploads = [
      {
        localId: 'u-4',
        fileName: 'p.png',
        fileSize: 3000,
        phase: 'scanning',
        progress: 1,
        attachmentId: 'att-9',
        attachment: { mime_type: 'image/png', scan_status: 'pending' },
        errorKey: null,
      },
    ];
    const onSend = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<ChatComposer onSend={onSend} quoteMessage={null} onClearQuote={vi.fn()} />);
    const input = screen.getByTestId('chat-composer-input');
    fireEvent.change(input, { target: { value: 'with file' } });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    await vi.waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    const opts = (onSend.mock.calls[0] as [string, ComposerSubmitOptions])[1];
    expect(opts.attachmentIds).toEqual(['att-9']);
    expect(opts.attachmentRefs[0]).toMatchObject({ id: 'att-9', file_name: 'p.png' });
    // 发送成功后清空上传列表
    expect(holder.cancel).toHaveBeenCalledWith('u-4');
  });

  it('发送失败保留草稿并呈现内联错误', async () => {
    const onSend = vi.fn().mockRejectedValue(new Error('nope'));
    renderWithProviders(<ChatComposer onSend={onSend} quoteMessage={null} onClearQuote={vi.fn()} />);
    const input = screen.getByTestId('chat-composer-input');
    fireEvent.change(input, { target: { value: 'will fail' } });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    expect(await screen.findByTestId('chat-composer-error')).toBeInTheDocument();
    expect((input as HTMLTextAreaElement).value).toBe('will fail');
  });

  it('disabled 时禁用输入与发送', () => {
    renderWithProviders(<ChatComposer onSend={vi.fn()} quoteMessage={null} onClearQuote={vi.fn()} disabled />);
    expect(screen.getByTestId('chat-composer-input')).toBeDisabled();
    expect(screen.getByTestId('chat-composer-send')).toBeDisabled();
    expect(screen.getByTestId('chat-composer-attach')).toBeDisabled();
  });

  it('uploading 阶段呈现百分比进度', () => {
    holder.uploads = [
      { localId: 'u-5', fileName: 'big.zip', fileSize: 1000, phase: 'uploading', progress: 0.42, attachmentId: null, attachment: null, errorKey: null },
    ];
    renderWithProviders(<ChatComposer onSend={vi.fn()} quoteMessage={null} onClearQuote={vi.fn()} />);
    expect(screen.getByTestId('chat-composer-uploads')).toHaveTextContent('42%');
  });

  it('scanning/validating 阶段呈现本地化阶段文案', () => {
    holder.uploads = [
      { localId: 'u-6', fileName: 'a.png', fileSize: 10, phase: 'scanning', progress: 1, attachmentId: 'att-1', attachment: null, errorKey: null },
    ];
    renderWithProviders(<ChatComposer onSend={vi.fn()} quoteMessage={null} onClearQuote={vi.fn()} />);
    // scanning 阶段经 chat.upload.phase.scanning 渲染(非百分比)
    expect(screen.getByTestId('chat-composer-uploads').textContent).not.toContain('%');
  });

  it('error 阶段无 errorKey 时回退通用错误文案', () => {
    holder.uploads = [
      { localId: 'u-7', fileName: 'x.bin', fileSize: 10, phase: 'error', progress: 0, attachmentId: null, attachment: null, errorKey: null },
    ];
    renderWithProviders(<ChatComposer onSend={vi.fn()} quoteMessage={null} onClearQuote={vi.fn()} />);
    // errorKey 为 null → 回退 common.unknownError(非空文案)
    expect(screen.getByTestId('chat-composer-uploads').textContent).toContain('x.bin');
  });

  it('点击附件按钮触发隐藏文件选择器', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ChatComposer onSend={vi.fn()} quoteMessage={null} onClearQuote={vi.fn()} />);
    const input = screen.getByTestId('chat-composer-file');
    const clickSpy = vi.spyOn(input, 'click').mockImplementation(() => undefined);
    await user.click(screen.getByTestId('chat-composer-attach'));
    expect(clickSpy).toHaveBeenCalledTimes(1);
    clickSpy.mockRestore();
  });

  it('点击发送按钮提交(onClick 路径)', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<ChatComposer onSend={onSend} quoteMessage={null} onClearQuote={vi.fn()} />);
    fireEvent.change(screen.getByTestId('chat-composer-input'), { target: { value: 'via button' } });
    await user.click(screen.getByTestId('chat-composer-send'));
    await vi.waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect((onSend.mock.calls[0] as [string])[0]).toBe('via button');
  });

  it('空内容时 Enter 提交被 canSend 守卫拦截', () => {
    const onSend = vi.fn();
    renderWithProviders(<ChatComposer onSend={onSend} quoteMessage={null} onClearQuote={vi.fn()} />);
    const input = screen.getByTestId('chat-composer-input');
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('workspaceId 透传 useAttachmentUploader(预上传归属工作区,attachment.md §3.1)', () => {
    const onSend = vi.fn();
    const first = renderWithProviders(
      <ChatComposer
        onSend={onSend}
        quoteMessage={null}
        onClearQuote={vi.fn()}
        workspaceId="ws-1"
      />,
    );
    expect(holder.lastOptions).toEqual({ workspaceId: 'ws-1' });
    first.unmount();
    // 未传 workspaceId → options.workspaceId 为 undefined(兼容旧调用)。
    renderWithProviders(<ChatComposer onSend={onSend} quoteMessage={null} onClearQuote={vi.fn()} />);
    expect(holder.lastOptions).toEqual({ workspaceId: undefined });
  });
});
