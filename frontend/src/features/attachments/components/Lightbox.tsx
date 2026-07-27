/**
 * 图片灯箱(attachment.md §4.3:查看原图,支持缩放/旋转/下载/在附件区定位)。
 * 经 Dialog 承载(焦点圈养 / Esc 关闭 / aria-modal)。原图签名 URL 由父级解析后传入;
 * url 为 null 时呈现加载中占位。缩放/旋转为纯 CSS transform(合成器友好,
 * 不动布局属性);每次重新打开重置视图。所有文案来自 prop,无硬编码可见字符串。
 */
import { useEffect, useState } from 'react';
import { Button, Dialog, IconButton, Skeleton } from '../../../design';
import '../attachments.css';

const MIN_SCALE = 0.5;
const MAX_SCALE = 4;
const SCALE_STEP = 0.5;

export interface LightboxProps {
  readonly open: boolean;
  /** 对话框标题(通常为文件名) */
  readonly title: string;
  /** 原图签名 URL;null 表示尚未解析(加载中) */
  readonly imageUrl: string | null;
  readonly loadingLabel: string;
  readonly downloadLabel: string;
  readonly closeLabel: string;
  readonly zoomInLabel: string;
  readonly zoomOutLabel: string;
  readonly rotateLabel: string;
  readonly resetLabel: string;
  readonly locateLabel: string;
  readonly onDownload: () => void;
  /** 在附件区定位(§4.3):关闭灯箱并滚动到对应条目。 */
  readonly onLocate: () => void;
  readonly onClose: () => void;
}

export function Lightbox(props: LightboxProps): React.JSX.Element {
  const [scale, setScale] = useState(1);
  const [rotation, setRotation] = useState(0);

  // 每次打开重置视图(上一张的缩放/旋转不残留)。
  useEffect(() => {
    if (props.open) {
      setScale(1);
      setRotation(0);
    }
  }, [props.open]);

  return (
    <Dialog open={props.open} onClose={props.onClose} title={props.title} closeLabel={props.closeLabel}>
      <div className="mesh-attachments-lightbox">
        <div className="mesh-attachments-lightbox__viewport">
          {props.imageUrl === null ? (
            <Skeleton loadingLabel={props.loadingLabel} className="mesh-attachments-lightbox__loading" />
          ) : (
            <img
              className="mesh-attachments-lightbox__image"
              src={props.imageUrl}
              alt={props.title}
              style={{ transform: `scale(${scale}) rotate(${rotation}deg)` }}
              data-testid="lightbox-image"
            />
          )}
        </div>
        <div className="mesh-attachments-lightbox__actions">
          <IconButton
            label={props.zoomOutLabel}
            size="sm"
            data-testid="lightbox-zoom-out"
            disabled={props.imageUrl === null || scale <= MIN_SCALE}
            onClick={() => setScale((value) => Math.max(MIN_SCALE, value - SCALE_STEP))}
          >
            <span aria-hidden="true">−</span>
          </IconButton>
          <IconButton
            label={props.zoomInLabel}
            size="sm"
            data-testid="lightbox-zoom-in"
            disabled={props.imageUrl === null || scale >= MAX_SCALE}
            onClick={() => setScale((value) => Math.min(MAX_SCALE, value + SCALE_STEP))}
          >
            <span aria-hidden="true">+</span>
          </IconButton>
          <IconButton
            label={props.rotateLabel}
            size="sm"
            data-testid="lightbox-rotate"
            disabled={props.imageUrl === null}
            onClick={() => setRotation((value) => (value + 90) % 360)}
          >
            <span aria-hidden="true">⟳</span>
          </IconButton>
          <IconButton
            label={props.resetLabel}
            size="sm"
            data-testid="lightbox-reset"
            disabled={props.imageUrl === null || (scale === 1 && rotation === 0)}
            onClick={() => {
              setScale(1);
              setRotation(0);
            }}
          >
            <span aria-hidden="true">↺</span>
          </IconButton>
          <Button variant="secondary" size="sm" onClick={props.onLocate} data-testid="lightbox-locate">
            {props.locateLabel}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={props.onDownload}
            disabled={props.imageUrl === null}
            data-testid="lightbox-download"
          >
            {props.downloadLabel}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
