/**
 * 聊天输入框(chat-session.md §4.2)。textarea + Cmd/Ctrl+Enter 发送;
 * 附件经 useAttachmentUploader **预上传**(不预关联,§2.4),发送时把就绪附件的
 * attachment_ids 随 sendMessage 一并发出,由后端在发送时关联(契约唯一关联时机);
 * 引用回复(quote_message_id)以顶部横幅呈现,可 × 取消;流式进行中由父级禁用输入,
 * foot 以 [■ 停止] 按钮替换发送按钮(spec §4.1 输入区)。
 * 数据获取/乐观在父级(onSend 返回 Promise);本组件只编排输入与上传态。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { Button, IconButton } from '../../design';
import { useT } from '../../i18n';
import { useAttachmentUploader } from '../attachments/useAttachmentUploader';
import type { UploadEntry } from '../attachments/types';
import { formatByteSize } from './MessageBubble';
import type { ChatAttachmentRef, ChatMessage } from './types';

/** 这些阶段的附件已落库可关联(scanning 字节已传完;ready 已放行)。 */
const LINKABLE_PHASES: ReadonlySet<UploadEntry['phase']> = new Set(['scanning', 'ready']);
/** 这些阶段仍在传输,发送须等待。 */
const PENDING_PHASES: ReadonlySet<UploadEntry['phase']> = new Set([
  'validating',
  'uploading',
  'completing',
]);

export interface ComposerSubmitOptions {
  readonly attachmentIds: readonly string[];
  /** 就绪附件的展示快照(乐观用户气泡即时呈现附件卡片)。 */
  readonly attachmentRefs: readonly ChatAttachmentRef[];
  readonly quoteMessageId: string | null;
}

export interface ChatComposerProps {
  /** 父级执行 sendMessage + 启动流;reject 时本组件保留草稿并呈现重试。 */
  readonly onSend: (content: string, opts: ComposerSubmitOptions) => Promise<void>;
  readonly quoteMessage: ChatMessage | null;
  readonly onClearQuote: () => void;
  /** 会话非 active 或流式进行中时禁用发送。 */
  readonly disabled?: boolean;
  /** 流式进行中(spec §4.1 输入区 [■ 停止]):foot 以停止按钮替换发送按钮。 */
  readonly isStreaming?: boolean;
  /** 停止当前生成(父级经独立幂等 stop 端点 + 本地拆流)。 */
  readonly onStop?: () => void;
  /** mod+↑ 编辑上一条:nonce 变化即以 content 预填草稿并聚焦(§4.3 S12)。 */
  readonly draftSeed?: { readonly nonce: number; readonly content: string } | null;
  /**
   * 预上传归属工作区(attachment.md §3.1):聊天附件预上传不预关联实体(§2.4
   * 发送时关联),此时 upload-requests 必带 workspace_id,否则后端 400
   * 「workspace_id is required when link_to is absent」。
   */
  readonly workspaceId?: string;
}

export function ChatComposer(props: ChatComposerProps): React.JSX.Element {
  const t = useT();
  const uploader = useAttachmentUploader({ workspaceId: props.workspaceId });
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [value, setValue] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState(false);

  // mod+↑「编辑上一条」草稿种子:nonce 变化即预填并聚焦。
  useEffect(() => {
    if (props.draftSeed === undefined || props.draftSeed === null) return;
    setValue(props.draftSeed.content);
    textareaRef.current?.focus();
  }, [props.draftSeed]);

  const isUploading = uploader.uploads.some((upload) => PENDING_PHASES.has(upload.phase));
  // 就绪附件(scanning/ready 且已得 attachmentId):id 用于发送关联,ref 用于乐观展示。
  const readyUploads = useMemo(
    () =>
      uploader.uploads.filter(
        (upload) => LINKABLE_PHASES.has(upload.phase) && upload.attachmentId !== null,
      ),
    [uploader.uploads],
  );
  const readyAttachmentIds = useMemo(
    () => readyUploads.map((upload) => upload.attachmentId as string),
    [readyUploads],
  );
  const readyAttachmentRefs = useMemo<readonly ChatAttachmentRef[]>(
    () =>
      readyUploads.map((upload) => ({
        id: upload.attachmentId as string,
        file_name: upload.fileName,
        mime_type: upload.attachment?.mime_type ?? null,
        byte_size: upload.fileSize,
        scan_status: upload.attachment?.scan_status ?? 'pending',
      })),
    [readyUploads],
  );
  const canSend = !props.disabled && !sending && !isUploading && value.trim() !== '';

  const clearUploads = useCallback(() => {
    for (const upload of uploader.uploads) uploader.cancel(upload.localId);
  }, [uploader]);

  const submit = useCallback(async () => {
    if (!canSend) return;
    setSending(true);
    setSendError(false);
    const quoteId = props.quoteMessage !== null ? props.quoteMessage.id : null;
    try {
      await props.onSend(value.trim(), {
        attachmentIds: readyAttachmentIds,
        attachmentRefs: readyAttachmentRefs,
        quoteMessageId: quoteId,
      });
      setValue('');
      clearUploads();
    } catch {
      // 具体错误码由父级 toast;此处仅置内联失败态供重试(保留草稿)。
      setSendError(true);
    } finally {
      setSending(false);
    }
  }, [canSend, props, value, readyAttachmentIds, readyAttachmentRefs, clearUploads]);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      // IME 组合输入豁免(评审 P1):候选词阶段的 Enter 是选词确认,不是发送。
      if (event.nativeEvent.isComposing || event.keyCode === 229) {
        return;
      }
      if (event.key === 'Enter') {
        // Enter 发送;Shift+Enter 换行(§4.3 S12);mod+Enter 同样发送(兼容旧肌肉记忆)。
        if (event.metaKey || event.ctrlKey || !event.shiftKey) {
          event.preventDefault();
          void submit();
        }
        return;
      }
      if (event.key === 'Escape') {
        // Esc 退出输入焦点(§4.3 S12)。
        event.currentTarget.blur();
      }
    },
    [submit],
  );

  const handleFiles = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files;
      if (files !== null && files.length > 0) uploader.addFiles(Array.from(files));
      event.target.value = '';
    },
    [uploader],
  );

  return (
    <div className="mesh-chat__composer" data-testid="chat-composer">
      {props.quoteMessage !== null ? (
        <div className="mesh-chat__composer-quote" data-testid="chat-composer-quote">
          <span className="mesh-chat__composer-quote-text">
            {t('chat.composer.quoting')}
            {props.quoteMessage.content.trim().slice(0, 60)}
          </span>
          <IconButton
            label={t('chat.composer.clearQuote')}
            data-testid="chat-composer-clear-quote"
            onClick={props.onClearQuote}
          >
            ×
          </IconButton>
        </div>
      ) : null}

      {uploader.uploads.length > 0 ? (
        <ul className="mesh-chat__composer-uploads" data-testid="chat-composer-uploads">
          {uploader.uploads.map((upload) => (
            <li
              key={upload.localId}
              className="mesh-chat__composer-upload"
              data-phase={upload.phase}
            >
              <span className="mesh-chat__composer-upload-name">{upload.fileName}</span>
              <span className="mesh-chat__composer-upload-meta">
                {upload.phase === 'error'
                  ? t(upload.errorKey ?? 'common.unknownError')
                  : upload.phase === 'uploading'
                    ? `${Math.round(upload.progress * 100)}%`
                    : t(`chat.upload.phase.${upload.phase}`)}
                {' · '}
                {formatByteSize(upload.fileSize)}
              </span>
              <IconButton
                label={t('chat.upload.cancel')}
                data-testid={`chat-upload-cancel-${upload.localId}`}
                onClick={() => uploader.cancel(upload.localId)}
              >
                ×
              </IconButton>
            </li>
          ))}
        </ul>
      ) : null}

      <textarea
        ref={textareaRef}
        className="mesh-chat__composer-input"
        data-testid="chat-composer-input"
        value={value}
        rows={3}
        placeholder={t('chat.composer.placeholder')}
        aria-label={t('chat.composer.placeholder')}
        disabled={props.disabled}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
      />

      <div className="mesh-chat__composer-foot">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          data-testid="chat-composer-file"
          className="mesh-chat__composer-file"
          onChange={handleFiles}
        />
        <Button
          variant="secondary"
          size="sm"
          data-testid="chat-composer-attach"
          disabled={props.disabled}
          onClick={() => fileInputRef.current?.click()}
        >
          {t('chat.composer.attach')}
        </Button>

        {sendError ? (
          <span
            className="mesh-chat__composer-error"
            role="alert"
            data-testid="chat-composer-error"
          >
            {t('chat.composer.sendFailed')}
          </span>
        ) : null}

        {props.isStreaming === true && props.onStop !== undefined ? (
          <Button
            variant="danger"
            size="sm"
            data-testid="chat-composer-stop"
            onClick={props.onStop}
          >
            {t('chat.action.stop')}
          </Button>
        ) : (
          <Button
            size="sm"
            data-testid="chat-composer-send"
            disabled={!canSend}
            isLoading={sending}
            onClick={() => void submit()}
          >
            {t('chat.composer.send')}
          </Button>
        )}
      </div>
    </div>
  );
}
