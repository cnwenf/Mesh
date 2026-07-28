/**
 * 消息附件渲染(chat-session.md §4.2 / attachment.md §3 可见性闸门)。
 * 复用 attachments 特性组件(FileIcon / Thumbnail / Lightbox)。扫描闸门:
 * 仅 clean/skipped 放行缩略图/预览/下载;pending/scanning 呈现「扫描中」占位(无下载);
 * infected(及其他非放行/非扫描态)呈现拦截态(无下载)。消息内联快照(ChatAttachmentRef)
 * 缺渲染字段(is_image/extension/签名 URL),放行项按需 getAttachment 取完整渲染对象(§3);
 * client 缺省时(纯展示)放行项退化为文件名 + 大小卡片。
 */
import { useCallback, useEffect, useState } from 'react';
import type { MeshApiClient } from '../../api';
import { MeshApiError, errorToI18nKey } from '../../api';
import { useToast } from '../../design';
import { useT } from '../../i18n';
import { getAttachment, getDownloadUrl } from '../attachments/api';
import { FileIcon } from '../attachments/components/FileIcon';
import { Lightbox } from '../attachments/components/Lightbox';
import { Thumbnail } from '../attachments/components/Thumbnail';
import type { Attachment } from '../attachments/types';
import { formatByteSize } from './format';
import type { ChatAttachmentRef } from './types';

/** 放行态:clean/skipped 开放下载/预览/缩略图(attachment.md §2.2)。 */
function isReleased(ref: ChatAttachmentRef): boolean {
  return ref.scan_status === 'clean' || ref.scan_status === 'skipped';
}

/** 扫描中:pending/scanning 尚不可下载(占位,无下载入口)。 */
function isScanning(ref: ChatAttachmentRef): boolean {
  return ref.scan_status === 'pending' || ref.scan_status === 'scanning';
}

/** 经签名 URL 触发浏览器下载。纵深防御:仅放行 http/https,杜绝 javascript:/data: 执行。 */
function triggerDownload(url: string, fileName: string): void {
  let protocol = '';
  try {
    protocol = new URL(url).protocol;
  } catch {
    return;
  }
  if (protocol !== 'http:' && protocol !== 'https:') return;
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

export interface MessageAttachmentsProps {
  readonly attachments: readonly ChatAttachmentRef[];
  readonly messageId: string;
  /** 缺省时放行项退化为名称 + 大小(纯展示,无下载/缩略图)。 */
  readonly client?: MeshApiClient;
}

export function MessageAttachments(props: MessageAttachmentsProps): React.JSX.Element {
  return (
    <ul className="mesh-chat__attachments" data-testid={`chat-attachments-${props.messageId}`}>
      {props.attachments.map((attachment) => (
        <MessageAttachmentItem key={attachment.id} attachment={attachment} client={props.client} />
      ))}
    </ul>
  );
}

interface MessageAttachmentItemProps {
  readonly attachment: ChatAttachmentRef;
  readonly client?: MeshApiClient;
}

/** 名称 + 大小退化卡片(扫描占位之外的兜底:无 client / 完整对象未就绪)。 */
function FallbackCard(props: { readonly attachment: ChatAttachmentRef }): React.JSX.Element {
  const { attachment } = props;
  return (
    <li className="mesh-chat__attachment" data-testid={`chat-attachment-file-${attachment.id}`}>
      <span className="mesh-chat__attachment-body">
        <FileIcon mimeType={attachment.mime_type} extension={null} isImage={false} />
        <span className="mesh-chat__attachment-name">{attachment.file_name}</span>
      </span>
      <span className="mesh-chat__attachment-size">{formatByteSize(attachment.byte_size)}</span>
    </li>
  );
}

function MessageAttachmentItem(props: MessageAttachmentItemProps): React.JSX.Element {
  const t = useT();
  const { attachment } = props;

  if (isScanning(attachment)) {
    return (
      <li
        className="mesh-chat__attachment"
        data-testid={`chat-attachment-scanning-${attachment.id}`}
      >
        <span className="mesh-chat__attachment-body">
          <FileIcon mimeType={attachment.mime_type} extension={null} isImage={false} />
          <span className="mesh-chat__attachment-name">{attachment.file_name}</span>
        </span>
        <span className="mesh-chat__attachment-status">{t('chat.attachment.scanning')}</span>
      </li>
    );
  }

  if (!isReleased(attachment)) {
    // infected(或其他非放行/非扫描态):拦截,绝不暴露下载。
    return (
      <li
        className="mesh-chat__attachment"
        data-testid={`chat-attachment-blocked-${attachment.id}`}
      >
        <span className="mesh-chat__attachment-body">
          <FileIcon mimeType={attachment.mime_type} extension={null} isImage={false} />
          <span className="mesh-chat__attachment-name">{attachment.file_name}</span>
        </span>
        <span className="mesh-chat__attachment-status mesh-chat__attachment-status--blocked">
          {t('chat.attachment.blocked')}
        </span>
      </li>
    );
  }

  if (props.client === undefined) {
    return <FallbackCard attachment={attachment} />;
  }

  return <ReleasedAttachment attachment={attachment} client={props.client} />;
}

interface ReleasedAttachmentProps {
  readonly attachment: ChatAttachmentRef;
  readonly client: MeshApiClient;
}

/** 放行附件:取完整渲染对象后,图片走缩略图 + 灯箱,非图片走文件卡;均带签名下载。 */
function ReleasedAttachment(props: ReleasedAttachmentProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const { attachment, client } = props;
  const [full, setFull] = useState<Attachment | null>(null);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);

  // 内联快照缺渲染字段:放行项按需 getAttachment(§3)以判图片/取签名 URL。
  useEffect(() => {
    let cancelled = false;
    getAttachment(client, attachment.id)
      .then((detail) => {
        if (!cancelled) setFull(detail);
      })
      .catch(() => {
        if (!cancelled) setFull(null);
      });
    return () => {
      cancelled = true;
    };
  }, [client, attachment.id]);

  const download = useCallback(async () => {
    try {
      const descriptor = await getDownloadUrl(client, attachment.id);
      triggerDownload(descriptor.url, descriptor.file_name);
    } catch (err: unknown) {
      const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
      toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
    }
  }, [client, attachment.id, toast, t]);

  const openLightbox = useCallback(() => {
    setLightboxOpen(true);
    setLightboxUrl(null);
    getDownloadUrl(client, attachment.id)
      .then((descriptor) => setLightboxUrl(descriptor.url))
      .catch(() => setLightboxUrl(null));
  }, [client, attachment.id]);

  // 完整对象未就绪(加载中 / 解析失败)→ 退化为名称 + 大小。
  if (full === null) {
    return <FallbackCard attachment={attachment} />;
  }

  const downloadButton = (
    <button
      type="button"
      className="mesh-chat__action"
      data-testid={`chat-attachment-download-${attachment.id}`}
      onClick={() => void download()}
    >
      {t('chat.attachment.download')}
    </button>
  );

  if (full.is_image) {
    return (
      <li
        className="mesh-chat__attachment mesh-chat__attachment-file"
        data-testid={`chat-attachment-image-${attachment.id}`}
      >
        <Thumbnail
          attachment={full}
          client={client}
          openLabel={t('chat.attachment.openImage')}
          loadingLabel={t('common.loading')}
          onOpen={openLightbox}
        />
        <span className="mesh-chat__attachment-name">{full.file_name}</span>
        {downloadButton}
        <Lightbox
          open={lightboxOpen}
          title={full.file_name}
          imageUrl={lightboxUrl}
          loadingLabel={t('common.loading')}
          downloadLabel={t('chat.attachment.download')}
          closeLabel={t('common.close')}
          zoomInLabel={t('attachments.zoomIn')}
          zoomOutLabel={t('attachments.zoomOut')}
          rotateLabel={t('attachments.rotate')}
          resetLabel={t('attachments.reset')}
          locateLabel={t('attachments.locate')}
          onDownload={() => void download()}
          onLocate={() => setLightboxOpen(false)}
          onClose={() => setLightboxOpen(false)}
        />
      </li>
    );
  }

  return (
    <li
      className="mesh-chat__attachment mesh-chat__attachment-file"
      data-testid={`chat-attachment-file-${attachment.id}`}
    >
      <FileIcon mimeType={full.mime_type} extension={full.extension} isImage={false} />
      <span className="mesh-chat__attachment-name">{full.file_name}</span>
      <span className="mesh-chat__attachment-size">{formatByteSize(full.file_size)}</span>
      {downloadButton}
    </li>
  );
}
