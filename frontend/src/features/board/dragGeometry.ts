/**
 * 拖拽几何命中检测(纯函数,design-quality §9.4)。
 *
 * 给定指针坐标与列/卡片测量矩形,计算:
 * - 指针落在哪一列(columnKey);
 * - 列内落点 index(卡片间中点法,与 computeDropPosition 对齐)。
 *
 * 纯模块:无 DOM 依赖、无副作用,便于单元测试。
 */

/** 列的测量矩形(含 columnKey 标识)。 */
export interface ColumnRect {
  readonly columnKey: string;
  readonly left: number;
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
}

/** 卡片的测量矩形(含 cardId 标识)。 */
export interface CardRect {
  readonly cardId: string;
  readonly top: number;
  readonly bottom: number;
}

/** 命中结果:目标列 + 列内插入 index(null 表示空列/列底)。 */
export interface HitResult {
  readonly columnKey: string;
  readonly index: number | null;
}

/** 点是否在矩形内(含边界)。 */
export function isPointInRect(
  x: number,
  y: number,
  rect: { left: number; top: number; right: number; bottom: number },
): boolean {
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
}

/**
 * 命中列:返回指针所在列的 columnKey(第一个包含该点的列)。
 * 若指针不在任何列范围内,返回 null。
 */
export function hitTestColumn(
  x: number,
  y: number,
  columns: readonly ColumnRect[],
): string | null {
  for (const col of columns) {
    if (isPointInRect(x, y, col)) return col.columnKey;
  }
  return null;
}

/**
 * 命中列内位置:浮点中点法(kanban §4.3)。
 * - 空列 → null(表示列底);
 * - 指针在某张卡片的上半部 → 该卡片 index(插入其前);
 * - 指针在某张卡片的下半部 → index+1(插入其后);
 * - 指针在所有卡片之下 → null(列底)。
 */
export function hitTestIndex(
  y: number,
  cards: readonly CardRect[],
): number | null {
  if (cards.length === 0) return null;
  for (let i = 0; i < cards.length; i++) {
    const rect = cards[i];
    const midpoint = (rect.top + rect.bottom) / 2;
    if (y < midpoint) return i;
  }
  return null;
}

/**
 * 组合命中检测:先找列,再找列内位置。
 * 若未命中任何列,返回 null。
 */
export function hitTest(
  x: number,
  y: number,
  columns: readonly ColumnRect[],
  cardsByColumn: Readonly<Record<string, readonly CardRect[]>>,
): HitResult | null {
  const columnKey = hitTestColumn(x, y, columns);
  if (columnKey === null) return null;
  const cards = cardsByColumn[columnKey] ?? [];
  return { columnKey, index: hitTestIndex(y, cards) };
}

/**
 * 判断拖拽是否超过移动阈值(约 6px,design-quality §9.4)。
 * 使用欧几里得距离;阈值可配置。
 */
export function exceedsDragThreshold(
  startX: number,
  startY: number,
  currentX: number,
  currentY: number,
  threshold = 6,
): boolean {
  const dx = currentX - startX;
  const dy = currentY - startY;
  return Math.sqrt(dx * dx + dy * dy) >= threshold;
}
