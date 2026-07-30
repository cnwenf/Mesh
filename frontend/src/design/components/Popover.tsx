/**
 * Popover(design-quality §7.5):承载次级上下文内容(筛选器、属性编辑、说明卡)
 * 的非模态浮层,与 Menu(仅操作项)分工:Popover 可含表单与富文本。
 *
 * - 触发器 aria-haspopup=dialog/aria-expanded/aria-controls;内容 role=dialog +
 *   aria-label;经 portal 渲染免裁切,surface-raised + shadow-2 + z-dropdown;
 * - 打开焦点进入内容(首个可聚焦元素,否则容器自身),关闭(Esc/点外)焦点归还
 *   触发器(§7.5 焦点返回);
 * - 定位:触发器下方 start/end 对齐,下方空间不足翻转向上,水平钳制在视口内。
 * 无硬编码可见文案。
 */
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import './components.css';

/** 视口边缘安全距与触发器间隙(px,布局常量非任意色值/间距 token 场景) */
const VIEWPORT_MARGIN_PX = 8;
const TRIGGER_GAP_PX = 4;
/** 默认内容宽(px):与 Menu 224px 同族,筛选/属性场景略宽 */
const DEFAULT_WIDTH_PX = 288;

export interface PopoverProps {
  /** 触发器内容(常为 IconButton/Button) */
  trigger: ReactNode;
  /** 触发器可访问名(aria-label) */
  triggerLabel: string;
  /** 浮层可访问名(role=dialog 的 aria-label,必填) */
  label: string;
  /** 相对触发器的水平对齐 */
  align?: 'start' | 'end';
  /** 内容宽度(px),默认 288;内容自适应可传 'auto' */
  width?: number | 'auto';
  /** 受控开关(缺省为非受控) */
  open?: boolean;
  /** 受控开关回调 */
  onOpenChange?: (next: boolean) => void;
  children: ReactNode;
}

interface Position {
  readonly top: number;
  readonly left: number;
  readonly placeAbove: boolean;
}

function computePosition(
  triggerRect: DOMRect,
  contentRect: { width: number; height: number },
  align: 'start' | 'end',
): Position {
  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  const spaceBelow = window.innerHeight - triggerRect.bottom;
  const placeAbove = spaceBelow < contentRect.height + TRIGGER_GAP_PX + VIEWPORT_MARGIN_PX && triggerRect.top > spaceBelow;
  const top = placeAbove
    ? triggerRect.top + scrollY - contentRect.height - TRIGGER_GAP_PX
    : triggerRect.bottom + scrollY + TRIGGER_GAP_PX;
  const rawLeft = align === 'start' ? triggerRect.left : triggerRect.right - contentRect.width;
  const maxLeft = window.innerWidth - contentRect.width - VIEWPORT_MARGIN_PX;
  const left = Math.min(Math.max(rawLeft + scrollX, VIEWPORT_MARGIN_PX + scrollX), maxLeft + scrollX);
  return { top, left, placeAbove };
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

export function Popover(props: PopoverProps): React.JSX.Element {
  const {
    trigger,
    triggerLabel,
    label,
    align = 'start',
    width = DEFAULT_WIDTH_PX,
    open: controlledOpen,
    onOpenChange,
    children,
  } = props;
  const popoverId = useId();
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const [position, setPosition] = useState<Position>({ top: 0, left: 0, placeAbove: false });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const setOpen = (next: boolean): void => {
    if (controlledOpen === undefined) setInternalOpen(next);
    onOpenChange?.(next);
  };

  const measureAndPlace = (): void => {
    const triggerEl = triggerRef.current;
    const contentEl = contentRef.current;
    if (triggerEl === null) return;
    const contentRect =
      contentEl !== null
        ? { width: contentEl.offsetWidth, height: contentEl.offsetHeight }
        : { width: DEFAULT_WIDTH_PX, height: 0 };
    setPosition(computePosition(triggerEl.getBoundingClientRect(), contentRect, align));
  };

  useLayoutEffect(() => {
    if (open) measureAndPlace();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 每次开合与对齐变化重算定位
  }, [open, align]);

  useEffect(() => {
    if (!open) return;
    // 下一帧聚焦:首个可聚焦元素,否则容器自身(§7.5 打开焦点进入)
    const handle = window.setTimeout(() => {
      const contentEl = contentRef.current;
      if (contentEl === null) return;
      const first = contentEl.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      (first ?? contentEl).focus();
    }, 0);
    return () => window.clearTimeout(handle);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: globalThis.MouseEvent): void => {
      const target = event.target;
      if (
        target instanceof Node &&
        !contentRef.current?.contains(target) &&
        !triggerRef.current?.contains(target)
      ) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: globalThis.KeyboardEvent): void => {
      if (event.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- open 翻转重挂监听;setOpen 经闭包稳定
  }, [open]);

  const toggle = (): void => {
    if (open) {
      setOpen(false);
      triggerRef.current?.focus();
    } else {
      measureAndPlace();
      setOpen(true);
    }
  };

  const widthStyle = width === 'auto' ? undefined : { inlineSize: `${width}px` };

  return (
    <span className="mesh-popover-anchor">
      <button
        ref={triggerRef}
        type="button"
        className="mesh-menu__trigger"
        aria-label={triggerLabel}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        onClick={toggle}
      >
        <span aria-hidden="true">{trigger}</span>
      </button>
      {open
        ? createPortal(
            <div
              ref={contentRef}
              id={popoverId}
              role="dialog"
              aria-label={label}
              tabIndex={-1}
              className={position.placeAbove ? 'mesh-popover mesh-popover--above' : 'mesh-popover'}
              style={{ top: position.top, left: position.left, ...widthStyle }}
            >
              {children}
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
