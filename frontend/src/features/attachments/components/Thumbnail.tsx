/**
 * 附件缩略图单元(attachment.md §4.3/§3.4):按需解析 md 档签名 URL 后加载 <img>。
 * M6 韧性:签名 URL 短时效(~60s),初次解析失败重试一次;<img> 加载失败
 * (onError:签名过期/对象更替)重新拉取新鲜签名 URL 再试,重取次数封顶避免
 * 死循环;重取期间回落加载占位。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { MeshApiClient } from '../../../api';
import { getThumbnailUrl } from '../api';
import type { Attachment } from '../types';

const THUMBNAIL_INITIAL_ATTEMPTS = 2;
const THUMBNAIL_MAX_REFETCHES = 2;

export interface ThumbnailProps {
  readonly attachment: Attachment;
  readonly client: MeshApiClient;
  readonly openLabel: string;
  readonly loadingLabel: string;
  readonly onOpen: (attachment: Attachment) => void;
}

/** 缩略图单元:解析签名 URL 前/重取中呈现占位;点击打开灯箱看原图。 */
export function Thumbnail(props: ThumbnailProps): React.JSX.Element {
  const { attachment, client } = props;
  const [url, setUrl] = useState<string | null>(null);
  const [refetchKey, setRefetchKey] = useState(0);
  const refetchCountRef = useRef(0);
  useEffect(() => {
    let cancelled = false;
    const load = async (attemptsLeft: number): Promise<void> => {
      try {
        const descriptor = await getThumbnailUrl(client, attachment.id, 'md');
        if (!cancelled) setUrl(descriptor.url);
      } catch {
        // 单次失败不再永久占位:重试一次,覆盖签名恰好在挂载瞬间过期的窗口。
        if (attemptsLeft > 0) await load(attemptsLeft - 1);
      }
    };
    void load(THUMBNAIL_INITIAL_ATTEMPTS - 1);
    return () => {
      cancelled = true;
    };
  }, [client, attachment.id, refetchKey]);
  const handleImageError = useCallback(() => {
    if (refetchCountRef.current >= THUMBNAIL_MAX_REFETCHES) return;
    refetchCountRef.current += 1;
    setUrl(null);
    setRefetchKey((key) => key + 1);
  }, []);
  return (
    <button
      type="button"
      className="mesh-attachments__thumb"
      aria-label={`${props.openLabel}: ${attachment.file_name}`}
      data-testid={`attachment-thumb-${attachment.id}`}
      onClick={() => props.onOpen(attachment)}
    >
      {url !== null ? (
        <img src={url} alt={attachment.file_name} loading="lazy" onError={handleImageError} />
      ) : (
        <span className="mesh-attachments__thumb-placeholder" aria-hidden="true" />
      )}
    </button>
  );
}
