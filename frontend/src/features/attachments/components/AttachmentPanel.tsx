/**
 * issue 附件区(attachment.md §4.1/§4.3/§4.4):
 * 图片走缩略图网格(灯箱看原图)、非图片走文件卡片列表;每项 hover 出现下载/删除/复制链接。
 * 可见性闸门(§2.2/§4.6):scan_status='pending' → 「扫描中」占位,不暴露下载;
 * infected/error → 拒绝态。agent 上传者带「AI」徽标(§4.4)。
 * 数据:listIssueAttachments 初载 + issue:{id} 频道 attachment.processed/deleted 帧合并。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { AttachmentComposer } from './AttachmentComposer';
import { FileIcon } from './FileIcon';
import { Lightbox } from './Lightbox';
import '../attachments.css';

/** 经签名 URL 触发浏览器下载(§4.5:直连对象存储)。
 *  纵深防御:仅放行 http/https 协议,杜绝服务端被攻陷时下发的
 *  javascript:/data: URL 经 anchor 执行。 */
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

/** 人性化文件大小(M2:不裸渲染字节数)。 */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded = unit === 0 ? String(Math.round(value)) : value.toFixed(value >= 100 ? 0 : 1);
  return `${rounded} ${units[unit]}`;
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

/** 头像占位:显示名首字符(人类/agent 一致;无显示名退化为 A)。 */
function avatarInitial(attachment: Attachment): string {
  const name = attachment.uploader?.display_name;
  if (name !== null && name !== undefined && name.trim() !== '') return name.trim().charAt(0).toUpperCase();
  return attachment.uploader?.member_type === 'agent' ? 'A' : '?';
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
  const [hasLoadError, setHasLoadError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [lightbox, setLightbox] = useState<Attachment | null>(null);
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setHasLoadError(false);
    void listIssueAttachments(client, props.issueId)
      .then((page) => {
        if (!cancelled) setAttachments([...page.data]);
      })
      .catch(() => {
        // M1:加载失败不静默吞掉——呈现错误态 + 重试入口。
        if (!cancelled) {
          setAttachments([]);
          setHasLoadError(true);
        }
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

  // L3:快速切换附件时,A 的慢响应不得落进 B 的弹窗——以 ref 记录当前灯箱
  // 附件 id,异步签名 URL 回来时校验归属,过期响应直接丢弃。
  const lightboxIdRef = useRef<string | null>(null);
  const openLightbox = useCallback(
    (attachment: Attachment) => {
      lightboxIdRef.current = attachment.id;
      setLightbox(attachment);
      setLightboxUrl(null);
      void getDownloadUrl(client, attachment.id)
        .then((descriptor) => {
          if (lightboxIdRef.current === attachment.id) setLightboxUrl(descriptor.url);
        })
        .catch(() => undefined);
    },
    [client],
  );
  const closeLightbox = useCallback(() => {
    lightboxIdRef.current = null;
    setLightbox(null);
  }, []);

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
        // L1:复制稳定的鉴权端点路径(而非 60s 短时效签名 URL)——
        // 粘贴分享的链接在点击时重新鉴权 + 重过扫描闸门,过期即废的问题也不复存在。
        await navigator.clipboard.writeText(`${env.apiBaseUrl}${attachment.download_url}`);
        toast.addToast(t('attachments.copiedToast'), {
          tone: 'success',
          closeLabel: t('common.close'),
        });
      } catch (err: unknown) {
        const key = err instanceof MeshApiError ? errorToI18nKey(err) : 'common.unknownError';
        toast.addToast(t(key), { tone: 'danger', closeLabel: t('common.close') });
      }
    },
    [toast, t],
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
            <span
              className="mesh-attachments__avatar"
              data-testid={`attachment-avatar-${attachment.id}`}
              aria-hidden="true"
            >
              {avatarInitial(attachment)}
            </span>
            {formatFileSize(attachment.file_size)} · {uploaderName(attachment, t('attachments.unknownUploader'))}
            {attachment.uploader?.member_type === 'agent' ? (
              <>
                <span className="mesh-attachments__ai-badge" data-testid={`attachment-ai-${attachment.id}`}>
                  {t('attachments.aiBadge')}
                </span>
                {/* §4.4:agent 产出物来源标记「来自 <agent> 运行」。 */}
                <span className="mesh-attachments__agent-source" data-testid={`attachment-agent-source-${attachment.id}`}>
                  {t('attachments.agentFrom', { name: uploaderName(attachment, t('attachments.aiBadge')) })}
                </span>
              </>
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
      {/* 上传入口(§4.1):回形针 / 拖拽 / 粘贴,直传完成后经 link_to 挂到本 issue;
          放行态经 attachment.processed 实时合并刷新。 */}
      <AttachmentComposer
        workspaceId={props.workspaceId}
        linkTo={{ type: 'issue', id: props.issueId }}
        client={client}
        onUploaded={() => setReloadKey((key) => key + 1)}
      />
      {hasLoadError ? (
        <p className="mesh-attachments__error" role="alert" data-testid="attachments-error">
          {t('attachments.loadError')}
          <button
            type="button"
            className="mesh-attachments__retry"
            data-testid="attachments-retry"
            onClick={() => setReloadKey((key) => key + 1)}
          >
            {t('attachments.retry')}
          </button>
        </p>
      ) : null}
      {!isLoading && !hasLoadError && attachments.length === 0 ? (
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
        zoomInLabel={t('attachments.zoomIn')}
        zoomOutLabel={t('attachments.zoomOut')}
        rotateLabel={t('attachments.rotate')}
        resetLabel={t('attachments.reset')}
        locateLabel={t('attachments.locate')}
        onDownload={() => {
          if (lightbox !== null) void download(lightbox);
        }}
        onLocate={() => {
          // §4.3「在附件区定位」:关闭灯箱并滚动到对应附件条目。
          if (lightbox === null) return;
          const id = lightbox.id;
          closeLightbox();
          requestAnimationFrame(() => {
            document
              .querySelector(`[data-testid="attachment-thumb-${id}"], [data-testid="attachment-file-${id}"]`)
              ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
          });
        }}
        onClose={closeLightbox}
      />
    </section>
  );
}
