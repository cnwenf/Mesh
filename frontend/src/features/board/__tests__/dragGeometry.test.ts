/**
 * 拖拽几何命中检测纯函数测试(design-quality §9.4):列/卡片矩形 → 命中列 + 列内
 * index(浮点中点法)、边界、空列、范围外、拖拽阈值。
 */
import { describe, expect, it } from 'vitest';
import {
  exceedsDragThreshold,
  hitTest,
  hitTestColumn,
  hitTestIndex,
  isPointInRect,
} from '../dragGeometry';
import type { CardRect, ColumnRect } from '../dragGeometry';

const columns: readonly ColumnRect[] = [
  { columnKey: 'todo', left: 0, top: 0, right: 100, bottom: 600 },
  { columnKey: 'done', left: 200, top: 0, right: 300, bottom: 600 },
];

describe('dragGeometry 命中检测', () => {
  it('isPointInRect:含边界为内,越界为外', () => {
    const rect = { left: 0, top: 0, right: 10, bottom: 10 };
    expect(isPointInRect(0, 0, rect)).toBe(true);
    expect(isPointInRect(10, 10, rect)).toBe(true);
    expect(isPointInRect(11, 5, rect)).toBe(false);
    expect(isPointInRect(5, -1, rect)).toBe(false);
  });

  it('hitTestColumn:命中第一个包含该点的列', () => {
    expect(hitTestColumn(50, 300, columns)).toBe('todo');
    expect(hitTestColumn(250, 300, columns)).toBe('done');
  });

  it('hitTestColumn:范围外返回 null', () => {
    expect(hitTestColumn(150, 300, columns)).toBeNull();
    expect(hitTestColumn(50, 700, columns)).toBeNull();
  });

  it('hitTestIndex:空列 → null(列底)', () => {
    expect(hitTestIndex(100, [])).toBeNull();
  });

  it('hitTestIndex:卡片上半 → 该卡 index;下半 → 越过该卡', () => {
    const cards: readonly CardRect[] = [
      { cardId: 'a', top: 100, bottom: 140 }, // 中点 120
      { cardId: 'b', top: 160, bottom: 200 }, // 中点 180
    ];
    expect(hitTestIndex(110, cards)).toBe(0); // a 上半
    expect(hitTestIndex(150, cards)).toBe(1); // a 下 / b 上
    expect(hitTestIndex(190, cards)).toBeNull(); // b 下半 → 列底
  });

  it('hitTest:组合列 + 列内位置', () => {
    const cardsByColumn = {
      done: [
        { cardId: 'c', top: 100, bottom: 140 },
        { cardId: 'd', top: 160, bottom: 200 },
      ] as readonly CardRect[],
    };
    expect(hitTest(250, 150, columns, cardsByColumn)).toEqual({ columnKey: 'done', index: 1 });
    expect(hitTest(250, 400, columns, cardsByColumn)).toEqual({ columnKey: 'done', index: null });
  });

  it('hitTest:未命中任何列 → null', () => {
    expect(hitTest(150, 150, columns, {})).toBeNull();
  });

  it('exceedsDragThreshold:欧氏距离 ≥ 阈值', () => {
    expect(exceedsDragThreshold(0, 0, 5, 0, 6)).toBe(false);
    expect(exceedsDragThreshold(0, 0, 6, 0, 6)).toBe(true);
    expect(exceedsDragThreshold(0, 0, 3, 4, 6)).toBe(false); // 3-4-5 距离 5 < 6
  });

  it('exceedsDragThreshold:自定义阈值', () => {
    expect(exceedsDragThreshold(0, 0, 3, 4, 5)).toBe(true);
    expect(exceedsDragThreshold(0, 0, 1, 1, 5)).toBe(false);
  });

  it('exceedsDragThreshold:省略阈值用默认 6', () => {
    expect(exceedsDragThreshold(0, 0, 7, 0)).toBe(true);
    expect(exceedsDragThreshold(0, 0, 5, 0)).toBe(false);
  });
});
