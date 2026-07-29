/**
 * 看板二维网格键盘移动(纯函数,search-command-palette.md §4.3 / S10 / 评审 P4)。
 *
 * - ↑↓(J/K)同列切换上一/下一张卡,列首/列尾停留不循环(clamp);
 * - ←→(H/L)跨列,落于目标列**纵向位置最近的卡**(目标列卡数更少取末卡);
 * - 目标列为空 → 穿透至该方向下一非空列;该方向无非空列 → 保持原选中;
 * - 全部列为空(或无卡)→ 返回 null,调用方保持原选中并忽略移动键;
 * - 无选中 + 首次移动 → 首个非空列首卡。
 */
import type { BoardCard } from './projection';
import type { BoardColumn } from './types';

export type BoardDirection = 'up' | 'down' | 'left' | 'right';

export interface BoardGridCell {
  readonly key: string;
  readonly cards: readonly BoardCard[];
}

export type BoardGrid = readonly BoardGridCell[];

/** 列骨架 + 卡片映射 → 网格(折叠列不参与键盘遍历)。 */
export function buildBoardGrid(
  columns: readonly BoardColumn[],
  cardsByKey: Readonly<Record<string, readonly BoardCard[]>>,
): BoardGrid {
  return columns
    .filter((column) => !column.collapsed)
    .map((column) => ({ key: column.key, cards: cardsByKey[column.key] ?? [] }));
}

/**
 * 求移动后的选中卡 id。返回 null 表示「保持原选中不变」(全空/边界穿透尽);
 * 入参 selectedCardId 为 null 时的首次移动返回首个非空列首卡。
 */
export function moveCardSelection(
  grid: BoardGrid,
  selectedCardId: string | null,
  direction: BoardDirection,
): string | null {
  if (grid.length === 0) {
    return null;
  }
  const nonEmpty = grid.filter((cell) => cell.cards.length > 0);
  if (nonEmpty.length === 0) {
    // 全部列为空:保持原选中不变并忽略移动键。
    return null;
  }
  if (selectedCardId === null) {
    return nonEmpty[0]?.cards[0]?.id ?? null;
  }

  const colIdx = grid.findIndex((cell) => cell.cards.some((card) => card.id === selectedCardId));
  if (colIdx < 0) {
    // 选中卡已不存在(实时移除等):重置于首个非空列首卡。
    return nonEmpty[0]?.cards[0]?.id ?? null;
  }
  const cell = grid[colIdx];
  if (cell === undefined) return null;
  const rowIdx = cell.cards.findIndex((card) => card.id === selectedCardId);

  if (direction === 'up') {
    return cell.cards[Math.max(0, rowIdx - 1)]?.id ?? selectedCardId;
  }
  if (direction === 'down') {
    return cell.cards[Math.min(cell.cards.length - 1, rowIdx + 1)]?.id ?? selectedCardId;
  }

  // 跨列:目标列空则穿透至该方向下一非空列。
  const step = direction === 'left' ? -1 : 1;
  let target = colIdx + step;
  while (target >= 0 && target < grid.length && grid[target]?.cards.length === 0) {
    target += step;
  }
  if (target < 0 || target >= grid.length) {
    return selectedCardId;
  }
  const targetCards = grid[target]?.cards;
  if (targetCards === undefined || targetCards.length === 0) {
    return selectedCardId;
  }
  // 落于目标列纵向最近卡:卡数更少时取末卡。
  const landIdx = Math.min(rowIdx, targetCards.length - 1);
  return targetCards[landIdx]?.id ?? selectedCardId;
}

/** 选中卡所在列的键(不在网格中 → null)。 */
export function columnKeyOfCard(grid: BoardGrid, cardId: string): string | null {
  const cell = grid.find((item) => item.cards.some((card) => card.id === cardId));
  return cell !== undefined ? cell.key : null;
}

/** 循环取下一列键(改状态键盘路径:状态列循环推进)。 */
export function nextColumnKey(grid: BoardGrid, currentKey: string): string | null {
  const nonEmptyOrAll = grid.length > 0 ? grid : [];
  const idx = nonEmptyOrAll.findIndex((cell) => cell.key === currentKey);
  if (idx < 0 || nonEmptyOrAll.length === 0) return null;
  return nonEmptyOrAll[(idx + 1) % nonEmptyOrAll.length]?.key ?? null;
}
