/**
 * issue 附件区(attachment.md §4.1/§4.3/§4.4):
 * 图片走缩略图网格(灯箱看原图)、非图片走文件卡片列表;每项 hover 出现下载/删除/复制链接。
 * 可见性闸门(§2.2/§4.6):scan_status='pending' → 「扫描中」占位,不暴露下载;
 * infected/error → 拒绝态。agent 上传者带「AI」徽标(§4.4)。
 * 数据:listIssueAttachments 初载 + issue:{id} 频道 attachment.processed/deleted 帧合并。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { MeshApiClient, MeshApiError, errorToI18nKey, getToken } from '../../../api';
import { IconButton, useToast } from '../../../design';
import { env } from '../../../env';
import { useT } from '../../../i18n';
import { useRealtimeContext } from '../../../shell/AppShell';
import {
  attachmentChannel,
  deleteAttachment,
  getDownloadUrl,
  getThumbnailUrl,
  listIssueAttachments,
} from '../api';
import { applyAttachmentDeleted, applyAttachmentProcessed } from '../realtime';
import type { Attachment } from '../types';
import { FileIcon } from './FileIcon';
import { Lightbox } from './Lightbox';
import '../attachments.css';

/** 经签名 URL 触发浏览器下载(§4.5:直连对象存储)。 */
function triggerDownload(url: string, fileName: string): void {
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

/** 放行态:clean/skipped 开放下载/预览(§2.2)。 */
function isReleased(attachment: Attachment): boolean {
  return attachment.scan_status === 'clean' || attachment.scan_status === 'skipped';
}

function isRejected(attachment: Attachment): boolean {
  return attachment.scan_status === 'infected' || attachment.scan_status === 'error';
}

function uploaderName(attachment: Attachment, fallback: string): string {
  return attachment.uploader?.display_name ?? fallback;
}

interface ThumbnailProps {
  readonly attachment: Attachment;
  readonly client: MeshApiClient;
  readonly openLabel: string;
  readonly loadingLabel: string;
  readonly onOpen: (attachment: Attachment) => void;
}

/** 缩略图单元:按需解析 md 签名 URL 后加载 <img>;解析前呈现占位。 */
function Thumbnail(props: ThumbnailProps): React.JSX.Element {
  const { attachment, client } = props;
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void getThumbnailUrl(client, attachment.id, 'md')
      .then((descriptor) => {
        if (!cancelled) setUrl(descriptor.url);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [client, attachment.id]);
  return (
    <button
      type="button"
      className="mesh-attachments__thumb"
      aria-label={`${props.openLabel}: ${attachment.file_name}`}
      data-testid={`attachment-thumb-${attachment.id}`}
      onClick={() => props.onOpen(attachment)}
    >
      {url !== null ? (
        <img src={url} alt={attachment.file_name} loading="lazy" />
      ) : (
        <span className="mesh-attachments__thumb-placeholder" aria-hidden="true" />
      )}
    </button>
  );
}

export interface AttachmentPanelProps {
  readonly workspaceId: string;
  readonly issueId: string;
  /** 注入客户端(测试);缺省按 env.apiBaseUrl + getToken 构建。 */
  readonly client?: MeshApiClient;
}

export function AttachmentPanel(props: AttachmentPanelProps): React.JSX.Element {
  const t = useT();
  const toast = useToast();
  const realtime = useRealtimeContext();
  const injectedClient = props.client;
  const client = useMemo(
    () => injectedClient ?? new MeshApiClient({ baseUrl: env.apiBaseUrl, getToken }),
    [injectedClient],
  );

  const [attachments, setAttachments] = useState<readonly Attachment[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const [lightbox, setLightbox] = useState<Attachment | null>(null);
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    void listIssueAttachments(client, props.issueId)
      .then((page) => {
        if (!cancelled) setAttachments([...page.data]);
      })
      .catch(() => {
        if (!cancelled) setAttachments([]);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, props.issueId, reloadKey]);

  // 实时合并:attachment.processed(放行)/ attachment.deleted(移除)。
  useEffect(() => {
    if (realtime === null) return;
    const channel = attachmentChannel(props.issueId);
    realtime.client.subscribe(channel);
    const off = realtime.client.onFrame((frame) => {
      setAttachments((prev) => {
        const processed = applyAttachmentProcessed(prev, frame);
        return applyAttachmentDeleted(processed, frame);
      });
    });
    return () => {
      off();
      realtime.client.unsubscribe(channel);
    };
  }, [realtime, props.issueId]);

  const openLightbox = useCallback(
    (attachment: Attachment) => {
      setLightbox(attachment);
      setLightboxUrl(null);
      void getDownloadUrl(client, attachment.id)
        .then((descriptor) => setLightboxUrl(descriptor.url))
        .catch(() => setLightboxUrl(null));
    },
    [client],
  );

  const download = useCallback(
    async (attachment: Attachment) => {
      try {
        const descriptor = await getDownloadUrl(client, attachment.id);
        triggerDownload(descriptor.url, descriptor.file_name);
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [client, toast, t],
  );

  const copyLink = useCallback(
    async (attachment: Attachment) => {
      try {
        const descriptor = await getDownloadUrl(client, attachment.id);
        await navigator.clipboard.writeText(descriptor.url);
        toast.addToast(t('attachments.copiedToast'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [client, toast, t],
  );

  const remove = useCallback(
    async (attachment: Attachment) => {
      // 乐观移除 + 失败回滚(§4.6 软删除)。
      setAttachments((prev) => prev.filter((item) => item.id !== attachment.id));
      try {
        await deleteAttachment(client, attachment.id);
      } catch (err: unknown) {
        setReloadKey((key) => key + 1);
        const errorKey = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
        toast.addToast(t(errorKey), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [client, toast, t],
  );

  const images = attachments.filter((item) => item.is_image);
  const files = attachments.filter((item) => !item.is_image);

  const renderActions = (attachment: Attachment): React.JSX.Element => (
    <span className="mesh-attachments__actions">
      <IconButton
        label={`${t('attachments.download')}: ${attachment.file_name}`}
        size="sm"
        data-testid={`attachment-download-${attachment.id}`}
        onClick={() => void download(attachment)}
      >
        <FileIcon mimeType={null} extension={null} isImage={false} className="mesh-attachments__action-glyph" />
      </IconButton>
      <IconButton
        label={`${t('attachments.copyLink')}: ${attachment.file_name}`}
        size="sm"
        data-testid={`attachment-copy-${attachment.id}`}
        onClick={() => void copyLink(attachment)}
      >
        <span aria-hidden="true">⧉</span>
      </IconButton>
      <IconButton
        label={`${t('attachments.delete')}: ${attachment.file_name}`}
        size="sm"
        variant="danger"
        data-testid={`attachment-delete-${attachment.id}`}
        onClick={() => void remove(attachment)}
      >
        <span aria-hidden="true">×</span>
      </IconButton>
    </span>
  );

  const renderFileCard = (attachment: Attachment): React.JSX.Element => {
    const released = isReleased(attachment);
    const rejected = isRejected(attachment);
    return (
      <li key={attachment.id} className="mesh-attachments__file" data-testid={`attachment-file-${attachment.id}`}>
        <FileIcon
          mimeType={attachment.mime_type}
          extension={attachment.extension}
          isImage={false}
          className="mesh-attachments__file-icon"
        />
        <span className="mesh-attachments__file-meta">
          <span className="mesh-attachments__file-name">{attachment.file_name}</span>
          <span className="mesh-attachments__file-sub">
            {attachment.file_size} · {uploaderName(attachment, t('attachments.unknownUploader'))}
            {attachment.uploader?.member_type === 'agent' ? (
              <span className="mesh-attachments__ai-badge" data-testid={`attachment-ai-${attachment.id}`}>
                {t('attachments.aiBadge')}
              </span>
            ) : null}
          </span>
          {!released && !rejected ? (
            <span className="mesh-attachments__scanning" data-testid={`attachment-scanning-${attachment.id}`}>
              {t('attachments.scanning')}
            </span>
          ) : null}
          {rejected ? (
            <span className="mesh-attachments__rejected" data-testid={`attachment-rejected-${attachment.id}`}>
              {t('attachments.rejected')}
            </span>
          ) : null}
        </span>
        {released ? renderActions(attachment) : null}
      </li>
    );
  };

  return (
    <section className="mesh-attachments" data-workspace-id={props.workspaceId} aria-label={t('attachments.title')}>
      <h2>
        {t('attachments.title')}（{attachments.length}）
      </h2>
      {!isLoading && attachments.length === 0 ? (
        <p className="mesh-attachments__empty" data-testid="attachments-empty">
          {t('attachments.empty')}
        </p>
      ) : null}
      {images.length > 0 ? (
        <ul className="mesh-attachments__grid" data-testid="attachments-grid">
          {images.map((attachment) => (
            <li key={attachment.id} className="mesh-attachments__grid-item">
              {isReleased(attachment) ? (
                <Thumbnail
                  attachment={attachment}
                  client={client}
                  openLabel={t('attachments.openImage')}
                  loadingLabel={t('common.loading')}
                  onOpen={openLightbox}
                />
              ) : (
                <span
                  className="mesh-attachments__scanning mesh-attachments__scanning--tile"
                  data-testid={`attachment-scanning-${attachment.id}`}
                >
                  {isRejected(attachment) ? t('attachments.rejected') : t('attachments.scanning')}
                </span>
              )}
              <span className="mesh-attachments__grid-name">{attachment.file_name}</span>
              {isReleased(attachment) ? renderActions(attachment) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {files.length > 0 ? (
        <ul className="mesh-attachments__files" data-testid="attachments-files">
          {files.map(renderFileCard)}
        </ul>
      ) : null}
      <Lightbox
        open={lightbox !== null}
        title={lightbox?.file_name ?? t('attachments.title')}
        imageUrl={lightboxUrl}
        loadingLabel={t('common.loading')}
        downloadLabel={t('attachments.download')}
        closeLabel={t('common.close')}
        onDownload={() => {
          if (lightbox !== null) void download(lightbox);
        }}
        onClose={() => setLightbox(null)}
      />
    </section>
  );
}
