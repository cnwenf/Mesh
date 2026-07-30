/**
 * 图片灯箱(attachment.md §4.3:查看原图,支持缩放/旋转/下载/在附件区定位)。
 * 经 Dialog 承载(焦点圈养 / Esc 关闭 / aria-modal)。原图签名 URL 由父级解析后传入;
 * url 为 null 时呈现加载中占位。缩放/旋转为纯 CSS transform(合成器友好,
 * 不动布局属性);每次重新打开重置视图。所有文案来自 prop,无硬编码可见字符串。
 */
import { useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { Button, Dialog, IconButton, Skeleton } from '../../../design';
import {
  doubleTapScale,
  LIGHTBOX_MAX_SCALE,
  LIGHTBOX_MIN_SCALE,
  pinchScale,
  pointerDistance,
} from '../pinchMath';
import type { PointerPoint } from '../pinchMath';
import '../attachments.css';

const MIN_SCALE = LIGHTBOX_MIN_SCALE;
const MAX_SCALE = LIGHTBOX_MAX_SCALE;
const SCALE_STEP = 0.5;
/** 双击判定窗口(ms):两次轻触间隔小于此值视为双击。 */
const DOUBLE_TAP_MS = 300;

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

  /* 触控手势(parity §2.22):双指捏合缩放 + 双击切换 1×↔2×。键盘等价路径为按钮,手势仅增强。 */
  const scaleRef = useRef(scale);
  scaleRef.current = scale;
  const pointersRef = useRef<Map<number, PointerPoint>>(new Map());
  const pinchStartRef = useRef<{ distance: number; base: number } | null>(null);
  const lastTapRef = useRef<{ time: number } | null>(null);

  const handlePointerDown = (event: ReactPointerEvent<HTMLImageElement>): void => {
    if (props.imageUrl === null) return;
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointersRef.current.size === 2) {
      const [a, b] = Array.from(pointersRef.current.values());
      pinchStartRef.current = { distance: pointerDistance(a, b), base: scaleRef.current };
    }
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLImageElement>): void => {
    if (!pointersRef.current.has(event.pointerId)) return;
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointersRef.current.size === 2 && pinchStartRef.current !== null) {
      const [a, b] = Array.from(pointersRef.current.values());
      const distance = pointerDistance(a, b);
      setScale(
        pinchScale(pinchStartRef.current.distance, distance, pinchStartRef.current.base, MIN_SCALE, MAX_SCALE),
      );
    }
  };

  const handlePointerEnd = (event: ReactPointerEvent<HTMLImageElement>): void => {
    const wasSingle = pointersRef.current.size === 1;
    pointersRef.current.delete(event.pointerId);
    if (pointersRef.current.size < 2) pinchStartRef.current = null;
    // 单指抬起且非捏合 → 双击检测。
    if (wasSingle && pinchStartRef.current === null) {
      const now = Date.now();
      const last = lastTapRef.current;
      if (last !== null && now - last.time < DOUBLE_TAP_MS) {
        setScale((current) => doubleTapScale(current));
        lastTapRef.current = null;
      } else {
        lastTapRef.current = { time: now };
      }
    }
  };

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
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerEnd}
              onPointerCancel={handlePointerEnd}
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
