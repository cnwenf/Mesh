/**
 * 指针拖拽控制器(design-quality §9.4 六规则)。
 *
 * 替换 HTML5 DnD:
 * - pointerdown 不立即拖;移动 ≥6px 阈值后进入拖拽;
 * - 触摸端长按 350ms(在 slop 内)也可进入拖拽;
 * - 源卡片原位保留占位(由渲染层据 dragState.cardId 呈现);
 * - 浮动副本跟随指针(渲染层 BoardDragLayer);目标列 droppable 态 + 落点指示线;
 * - WIP 预检:warn → 警告条(允许落);block 且 count>=limit → 危险条(禁落);
 * - Esc 取消;aria-live 播报各阶段。
 *
 * 说明:以 document 级 pointermove/pointerup 监听追踪指针(而非 setPointerCapture),
 * 二者对跨元素拖拽等效,且 document 监听在 jsdom 测试中更稳定、可预测。
 *
 * 本 hook 仅管理拖拽状态机;视觉反馈由 BoardColumns / BoardDragLayer 负责。
 * 易变回调经 ref 持有;document 监听经 tracking effect 挂卸,身份稳定,杜绝陈旧闭包。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { CardRect, ColumnRect, HitResult } from './dragGeometry';
import { exceedsDragThreshold, hitTest } from './dragGeometry';

/** 拖拽状态(供渲染层呈现浮层/占位/高亮/指示线)。 */
export interface DragState {
  readonly cardId: string;
  readonly cardIdentifier: string;
  readonly sourceRect: DOMRect;
  readonly pointerX: number;
  readonly pointerY: number;
  readonly hit: HitResult | null;
  readonly isBlocked: boolean;
  readonly isWarn: boolean;
}

/** 拖拽事件回调(经 ref 持有,渲染层每次渲染更新)。 */
export interface BoardDragCallbacks {
  readonly onDropCard: (issueId: string, toGroupKey: string, position: number) => void;
  readonly computePosition: (columnKey: string, index: number | null) => number;
  readonly isColumnBlocked: (columnKey: string) => boolean;
  readonly isColumnWarn: (columnKey: string) => boolean;
  readonly getColumnLabel: (columnKey: string) => string;
  readonly announce: (message: string) => void;
  readonly t: (key: string, values?: Record<string, unknown>) => string;
  /** 触摸长按触发(coarse pointer,§8.3):打开列目标 sheet,而非浮动副本拖拽。 */
  readonly onLongPress: (cardId: string) => void;
}

const DRAG_THRESHOLD = 6;
const LONG_PRESS_MS = 350;

interface PendingDrag {
  readonly cardId: string;
  readonly identifier: string;
  readonly startX: number;
  readonly startY: number;
  readonly rect: DOMRect;
  timer: ReturnType<typeof setTimeout> | null;
}

interface BoardDragHandlers {
  move: (event: PointerEvent) => void;
  up: () => void;
  key: (event: KeyboardEvent) => void;
}

export interface UseBoardDragResult {
  readonly dragState: DragState | null;
  readonly onPointerDown: (
    event: React.PointerEvent,
    cardId: string,
    cardIdentifier: string,
  ) => void;
}

export function useBoardDrag(
  enabled: boolean,
  callbacks: BoardDragCallbacks,
  getColumnRects: () => readonly ColumnRect[],
  getCardRects: (columnKey: string) => readonly CardRect[],
): UseBoardDragResult {
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [tracking, setTracking] = useState(false);

  // 易变依赖经 ref 持有 → document 处理器恒读最新值。
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;
  const getColumnRectsRef = useRef(getColumnRects);
  getColumnRectsRef.current = getColumnRects;
  const getCardRectsRef = useRef(getCardRects);
  getCardRectsRef.current = getCardRects;

  const pendingRef = useRef<PendingDrag | null>(null);
  const activeRef = useRef(false);
  const dragStateRef = useRef<DragState | null>(null);
  /** 上一次播报的命中列(避免每帧重复播报,§9.4 目标变化才播报)。 */
  const lastHitColumnRef = useRef<string | null>(null);

  const commit = useCallback((next: DragState | null) => {
    dragStateRef.current = next;
    setDragState(next);
  }, []);

  const clearPending = useCallback(() => {
    if (pendingRef.current?.timer != null) clearTimeout(pendingRef.current.timer);
    pendingRef.current = null;
  }, []);

  // 处理器闭包经 ref 暴露,供 tracking effect 内的稳定包装器调用。
  const handlersRef = useRef<BoardDragHandlers>(null as unknown as BoardDragHandlers);

  handlersRef.current = {
    move: (event: PointerEvent): void => {
      const pending = pendingRef.current;
      if (pending === null) return;
      if (!activeRef.current) {
        if (exceedsDragThreshold(pending.startX, pending.startY, event.clientX, event.clientY, DRAG_THRESHOLD)) {
          if (pending.timer != null) clearTimeout(pending.timer);
          activeRef.current = true;
          lastHitColumnRef.current = null;
          commit({
            cardId: pending.cardId,
            cardIdentifier: pending.identifier,
            sourceRect: pending.rect,
            pointerX: event.clientX,
            pointerY: event.clientY,
            hit: null,
            isBlocked: false,
            isWarn: false,
          });
          callbacksRef.current.announce(
            callbacksRef.current.t('board.dragStarted', { identifier: pending.identifier }),
          );
        }
        return;
      }
      const columns = getColumnRectsRef.current();
      const cardsByColumn: Record<string, readonly CardRect[]> = {};
      for (const col of columns) cardsByColumn[col.columnKey] = getCardRectsRef.current(col.columnKey);
      const hit = hitTest(event.clientX, event.clientY, columns, cardsByColumn);
      const prev = dragStateRef.current;
      if (prev === null) return;
      // 目标列变化才播报(§9.4),避免每帧刷屏。
      if (hit !== null && hit.columnKey !== lastHitColumnRef.current) {
        lastHitColumnRef.current = hit.columnKey;
        const count = getCardRectsRef.current(hit.columnKey).length;
        callbacksRef.current.announce(
          callbacksRef.current.t('board.dragOver', {
            column: callbacksRef.current.getColumnLabel(hit.columnKey),
            position: (hit.index ?? count) + 1,
          }),
        );
      }
      if (hit === null) lastHitColumnRef.current = null;
      commit({
        ...prev,
        pointerX: event.clientX,
        pointerY: event.clientY,
        hit,
        isBlocked: hit !== null && callbacksRef.current.isColumnBlocked(hit.columnKey),
        isWarn: hit !== null && callbacksRef.current.isColumnWarn(hit.columnKey),
      });
    },
    up: (): void => {
      clearPending();
      if (activeRef.current) {
        activeRef.current = false;
        lastHitColumnRef.current = null;
        const prev = dragStateRef.current;
        if (prev !== null && prev.hit !== null && !prev.isBlocked) {
          const position = callbacksRef.current.computePosition(prev.hit.columnKey, prev.hit.index);
          callbacksRef.current.onDropCard(prev.cardId, prev.hit.columnKey, position);
          callbacksRef.current.announce(
            callbacksRef.current.t('board.dragDropped', { identifier: prev.cardIdentifier }),
          );
        }
        commit(null);
      }
      setTracking(false);
    },
    key: (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return;
      clearPending();
      if (activeRef.current) {
        activeRef.current = false;
        lastHitColumnRef.current = null;
        const prev = dragStateRef.current;
        if (prev !== null) {
          callbacksRef.current.announce(
            callbacksRef.current.t('board.dragCancelled', { identifier: prev.cardIdentifier }),
          );
        }
        commit(null);
      }
      setTracking(false);
    },
  };

  // 卸载时清掉长按计时器,杜绝卸载后 setState。
  useEffect(() => clearPending, [clearPending]);

  // tracking 期间挂 document 监听;结束(或卸载)即卸,包装器身份稳定。
  useEffect(() => {
    if (!tracking) return;
    const move = (event: PointerEvent): void => handlersRef.current.move(event);
    const up = (): void => handlersRef.current.up();
    const key = (event: KeyboardEvent): void => handlersRef.current.key(event);
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      document.removeEventListener('keydown', key);
    };
  }, [tracking]);

  const onPointerDown = useCallback(
    (event: React.PointerEvent, cardId: string, cardIdentifier: string) => {
      if (!enabledRef.current) return;
      if (event.button !== 0 && event.pointerType === 'mouse') return;
      const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
      const startX = event.clientX;
      const startY = event.clientY;
      let timer: ReturnType<typeof setTimeout> | null = null;
      if (event.pointerType === 'touch') {
        // 触摸长按(350ms,slop 内)→ 打开列目标 sheet(§8.3),不进浮动副本拖拽。
        // 计时器仅在 pending 存活时触发(越阈值/抬起/卸载均先 clearTimeout),故无需空判。
        timer = setTimeout(() => {
          pendingRef.current = null;
          callbacksRef.current.onLongPress(cardId);
        }, LONG_PRESS_MS);
      }
      pendingRef.current = { cardId, identifier: cardIdentifier, startX, startY, rect, timer };
      setTracking(true);
    },
    [],
  );

  return { dragState, onPointerDown };
}
