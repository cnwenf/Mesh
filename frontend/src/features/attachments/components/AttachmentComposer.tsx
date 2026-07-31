/**
 * 附件上传入口(attachment.md §4.1/§4.2):回形针 → 文件选择器;拖拽到区域、粘贴截图
 * (Ctrl+V)直接触发上传。每文件一张进度卡片(进度条 + 取消;失败显示重试)。
 * 提交门控(§4.2 默认):全部上传完成方允许提交——进行中(validating/uploading/completing)
 * 时提交按钮禁用。导出供评论模块(MES-58)消费。
 */
import { useCallback, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, ClipboardEvent, DragEvent } from 'react';
import type { MeshApiClient } from '../../../api';
import { Button, Icon, IconButton } from '../../../design';
import { useT } from '../../../i18n';
import { formatFileSize } from '../format';
import { useAttachmentUploader } from '../useAttachmentUploader';
import type { Attachment, AttachmentLinkTo, UploadEntry } from '../types';
import { FileIcon } from './FileIcon';
import { ProgressRing } from './ProgressRing';
import '../attachments.css';

/** 进行中阶段:这些阶段内提交按钮禁用(§4.2 全部完成方可提交)。 */
const IN_FLIGHT_PHASES: ReadonlySet<UploadEntry['phase']> = new Set([
  'validating',
  'uploading',
  'completing',
]);

function fileKey(name: string, size: number): string {
  return `${name}::${size}`;
}

export interface AttachmentComposerProps {
  readonly workspaceId: string;
  readonly linkTo: AttachmentLinkTo;
  /** 提交(全部完成)后回调已完成附件;父级据此带 attachment_ids 提交评论。 */
  readonly onUploaded?: (attachments: readonly Attachment[]) => void;
  /** 注入客户端(测试);透传给 useAttachmentUploader。 */
  readonly client?: MeshApiClient;
}

export function AttachmentComposer(props: AttachmentComposerProps): React.JSX.Element {
  const t = useT();
  const uploader = useAttachmentUploader({ client: props.client });
  const inputRef = useRef<HTMLInputElement>(null);
  const filesRef = useRef<Map<string, File>>(new Map());
  const [isDragging, setIsDragging] = useState(false);

  const rememberFiles = useCallback((files: readonly File[]) => {
    for (const file of files) filesRef.current.set(fileKey(file.name, file.size), file);
  }, []);

  const ingest = useCallback(
    (files: readonly File[]) => {
      if (files.length === 0) return;
      rememberFiles(files);
      uploader.addFiles(files, props.linkTo);
    },
    [rememberFiles, uploader, props.linkTo],
  );

  const handleInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      ingest(files);
      // 复位以允许重复选择同一文件(change 仅在值变化时触发)。
      event.target.value = '';
    },
    [ingest],
  );

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragging(false);
      ingest(Array.from(event.dataTransfer.files ?? []));
    },
    [ingest],
  );

  const handlePaste = useCallback(
    (event: ClipboardEvent<HTMLDivElement>) => {
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length > 0) {
        event.preventDefault();
        ingest(files);
      }
    },
    [ingest],
  );

  const retry = useCallback(
    (entry: UploadEntry) => {
      const file = filesRef.current.get(fileKey(entry.fileName, entry.fileSize));
      uploader.cancel(entry.localId);
      if (file !== undefined) uploader.addFiles([file], props.linkTo);
    },
    [uploader, props.linkTo],
  );

  const completedAttachments = useMemo(
    () =>
      uploader.uploads
        .filter((entry) => (entry.phase === 'ready' || entry.phase === 'scanning') && entry.attachment !== null)
        .map((entry) => entry.attachment as Attachment),
    [uploader.uploads],
  );
  const isUploading = uploader.uploads.some((entry) => IN_FLIGHT_PHASES.has(entry.phase));
  const canSubmit = !isUploading && completedAttachments.length > 0;

  const submit = useCallback(() => {
    if (!canSubmit) return;
    props.onUploaded?.(completedAttachments);
  }, [canSubmit, completedAttachments, props]);

  const renderCard = (entry: UploadEntry): React.JSX.Element => {
    const percent = Math.round(entry.progress * 100);
    const isError = entry.phase === 'error';
    // validating/completing 无可靠字节进度 → 不确定环(§3.2 进度环)。
    const isIndeterminate = entry.phase === 'validating' || entry.phase === 'completing';
    const ringLabel =
      entry.phase === 'validating'
        ? t('attachments.validating')
        : entry.phase === 'completing'
          ? t('attachments.completing')
          : `${entry.fileName}: ${percent}%`;
    return (
      <li key={entry.localId} className="mesh-attachments-composer__card" data-testid={`upload-card-${entry.localId}`}>
        {isError ? (
          <FileIcon mimeType={null} extension={null} isImage={false} className="mesh-attachments-composer__card-icon" />
        ) : (
          <ProgressRing
            value={isIndeterminate ? 0 : percent}
            indeterminate={isIndeterminate}
            label={ringLabel}
            size={40}
          />
        )}
        <span className="mesh-attachments-composer__card-body">
          <span className="mesh-attachments-composer__card-name">{entry.fileName}</span>
          <span className="mesh-attachments-composer__card-size mesh-tnum">
            {formatFileSize(entry.fileSize)}
          </span>
          {isError ? (
            /* 失败卡(§3.2 / parity §2.22):保留文件名/大小 + 具名错误(error.* 四部分文案)+ 重试/移除。 */
            <span className="mesh-attachments-composer__error" role="alert" data-testid={`upload-error-${entry.localId}`}>
              {t(entry.errorKey ?? 'common.unknownError')}
            </span>
          ) : entry.phase === 'scanning' ? (
            <span className="mesh-attachments__scanning">{t('attachments.scanning')}</span>
          ) : null}
        </span>
        {isError ? (
          <span className="mesh-attachments-composer__card-actions">
            <Button size="sm" onClick={() => retry(entry)} data-testid={`upload-retry-${entry.localId}`}>
              {t('attachments.retry')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => uploader.cancel(entry.localId)}
              data-testid={`upload-cancel-${entry.localId}`}
            >
              {t('attachments.remove')}
            </Button>
          </span>
        ) : (
          <IconButton
            label={`${t('common.cancel')}: ${entry.fileName}`}
            size="sm"
            data-testid={`upload-cancel-${entry.localId}`}
            onClick={() => uploader.cancel(entry.localId)}
          >
            <Icon name="close" size={16} />
          </IconButton>
        )}
      </li>
    );
  };

  return (
    <div
      className={
        isDragging
          ? 'mesh-attachments-composer mesh-attachments-composer--dragging'
          : 'mesh-attachments-composer'
      }
      data-workspace-id={props.workspaceId}
      onDrop={handleDrop}
      onDragOver={(event) => {
        event.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onPaste={handlePaste}
      data-testid="attachment-composer"
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        className="mesh-attachments-composer__input"
        data-testid="attachment-file-input"
        onChange={handleInputChange}
      />
      <IconButton
        label={t('attachments.addFiles')}
        size="sm"
        data-testid="attachment-paperclip"
        onClick={() => inputRef.current?.click()}
      >
        <Icon name="paperclip" size={20} />
      </IconButton>
      <p className="mesh-attachments-composer__hint">{t('attachments.dropHint')}</p>
      {uploader.uploads.length > 0 ? (
        <ul className="mesh-attachments-composer__cards" data-testid="upload-cards">
          {uploader.uploads.map(renderCard)}
        </ul>
      ) : null}
      <Button
        size="sm"
        disabled={!canSubmit}
        data-testid="attachment-submit"
        aria-disabled={!canSubmit}
        onClick={submit}
      >
        {t('attachments.confirm')}
      </Button>
    </div>
  );
}
