/**
 * 键盘移动模式(design-quality §9.4.5 / §10.2 非拖拽替代路径)。
 *
 * 聚焦卡片后按方向键进入移动模式:
 * - ←/→ 选目标列(循环);
 * - ↑/↓ 选列内插入位置(0 = 顶部 … cards.length = 底部);
 * - Enter 确认 → onDropCard;
 * - Esc 取消。
 *
 * 播报经 aria-live 区域(board-live);文案一律走 i18n key(board.*)。
 */
import { useCallback, useState } from 'react';

export type TranslateFn = (key: string, values?: Record<string, unknown>) => string;

/** 键盘移动状态。 */
export interface KeyboardMoveState {
  readonly cardId: string;
  readonly cardIdentifier: string;
  readonly targetColumnKey: string;
  /** 目标列内插入 index(0 = 顶部, cards.length = 底部)。 */
  readonly targetIndex: number;
}

export interface UseBoardKeyboardMoveOptions {
  readonly enabled: boolean;
  readonly columns: readonly string[];
  readonly getCardCount: (columnKey: string) => number;
  readonly getColumnLabel: (columnKey: string) => string;
  readonly onDropCard: (issueId: string, toGroupKey: string, position: number) => void;
  readonly computePosition: (columnKey: string, index: number | null) => number;
  readonly announce: (message: string) => void;
  readonly t: TranslateFn;
}

export interface UseBoardKeyboardMoveResult {
  readonly moveState: KeyboardMoveState | null;
  readonly handleCardKeyDown: (
    event: React.KeyboardEvent,
    cardId: string,
    cardIdentifier: string,
    currentColumnKey: string,
  ) => void;
  readonly cancelMove: () => void;
}

const ARROW_KEYS = new Set(['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']);

export function useBoardKeyboardMove(
  options: UseBoardKeyboardMoveOptions,
): UseBoardKeyboardMoveResult {
  const {
    enabled,
    columns,
    getCardCount,
    getColumnLabel,
    onDropCard,
    computePosition,
    announce,
    t,
  } = options;
  const [moveState, setMoveState] = useState<KeyboardMoveState | null>(null);

  const cancelMove = useCallback(() => {
    setMoveState((prev) => {
      if (prev !== null) announce(t('board.moveCancelled'));
      return null;
    });
  }, [announce, t]);

  const announceTarget = useCallback(
    (columnKey: string, index: number) => {
      const count = getCardCount(columnKey);
      announce(
        t('board.moveTargetColumn', {
          column: getColumnLabel(columnKey),
          index: index + 1,
          total: count + 1,
        }),
      );
    },
    [announce, t, getCardCount, getColumnLabel],
  );

  const handleCardKeyDown = useCallback(
    (
      event: React.KeyboardEvent,
      cardId: string,
      cardIdentifier: string,
      currentColumnKey: string,
    ) => {
      if (!enabled || !ARROW_KEYS.has(event.key) && event.key !== 'Enter' && event.key !== 'Escape') {
        return;
      }

      // 未进入移动模式:方向键进入。
      if (moveState === null) {
        if (!ARROW_KEYS.has(event.key)) return;
        event.preventDefault();
        const count = getCardCount(currentColumnKey);
        const initialIndex = Math.min(1, count);
        setMoveState({
          cardId,
          cardIdentifier,
          targetColumnKey: currentColumnKey,
          targetIndex: initialIndex,
        });
        // 进入时仅播报说明;目标列/位置随后续方向键播报,避免覆盖说明文案。
        announce(t('board.moveModeEntered', { identifier: cardIdentifier }));
        return;
      }

      // 已在移动模式:仅响应当前卡片。
      if (moveState.cardId !== cardId) return;
      event.preventDefault();

      if (event.key === 'Escape') {
        cancelMove();
        return;
      }

      if (event.key === 'Enter') {
        const count = getCardCount(moveState.targetColumnKey);
        const index = moveState.targetIndex >= count ? null : moveState.targetIndex;
        const position = computePosition(moveState.targetColumnKey, index);
        onDropCard(moveState.cardId, moveState.targetColumnKey, position);
        announce(
          t('board.moveConfirmed', {
            identifier: moveState.cardIdentifier,
            column: getColumnLabel(moveState.targetColumnKey),
          }),
        );
        setMoveState(null);
        return;
      }

      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        const idx = columns.indexOf(moveState.targetColumnKey);
        const next =
          event.key === 'ArrowRight'
            ? (idx + 1) % columns.length
            : (idx - 1 + columns.length) % columns.length;
        const nextKey = columns[next] ?? moveState.targetColumnKey;
        const count = getCardCount(nextKey);
        const nextIndex = Math.min(moveState.targetIndex, count);
        setMoveState({ ...moveState, targetColumnKey: nextKey, targetIndex: nextIndex });
        announceTarget(nextKey, nextIndex);
        return;
      }

      // ArrowUp / ArrowDown:调整列内位置。
      const count = getCardCount(moveState.targetColumnKey);
      const nextIndex =
        event.key === 'ArrowDown'
          ? Math.min(moveState.targetIndex + 1, count)
          : Math.max(moveState.targetIndex - 1, 0);
      setMoveState({ ...moveState, targetIndex: nextIndex });
      announceTarget(moveState.targetColumnKey, nextIndex);
    },
    [
      enabled,
      moveState,
      columns,
      getCardCount,
      getColumnLabel,
      onDropCard,
      computePosition,
      announce,
      announceTarget,
      cancelMove,
      t,
    ],
  );

  return { moveState, handleCardKeyDown, cancelMove };
}
