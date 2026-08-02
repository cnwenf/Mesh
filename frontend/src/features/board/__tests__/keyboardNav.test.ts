/**
 * 看板二维网格键盘移动(§4.3 S10 / 评审 P4):列内 clamp、跨列最近行落点、
 * 空列穿透、全空保持、首次移动落首列首卡。
 */
import { describe, expect, it } from 'vitest';
import type { BoardCard } from '../projection';
import type { BoardColumn } from '../types';
import { buildBoardGrid, columnKeyOfCard, moveCardSelection, nextColumnKey } from '../keyboardNav';

function card(id: string): BoardCard {
  return {
    id,
    identifier: 'K-' + id,
    title: 'card ' + id,
    state_category: 'todo',
    status: null,
    status_id: null,
    priority: 'medium',
    assignee: null,
    assignee_id: null,
    project_id: null,
    position: Number(id),
    version: 1,
    updated_at: '2026-07-29T00:00:00Z',
  } as unknown as BoardCard;
}

function column(key: string, collapsed = false): BoardColumn {
  return { key, label: key, collapsed, wip: null, count: 0, placeholder: false } as BoardColumn;
}

// A:3 卡(a1..a3) · B:空列 · C:2 卡(c1..c2)
const COLUMNS = [column('A'), column('B'), column('C')];
const CARDS_BY_KEY: Record<string, BoardCard[]> = {
  A: [card('a1'), card('a2'), card('a3')],
  B: [],
  C: [card('c1'), card('c2')],
};
const GRID = buildBoardGrid(COLUMNS, CARDS_BY_KEY);

describe('moveCardSelection(二维网格移动)', () => {
  it('无选中 + 首次移动 → 首个非空列首卡', () => {
    expect(moveCardSelection(GRID, null, 'down')).toBe('a1');
    expect(moveCardSelection(GRID, null, 'right')).toBe('a1');
  });

  it('↑↓ 同列切换,列首/列尾停留不循环(clamp)', () => {
    expect(moveCardSelection(GRID, 'a1', 'up')).toBe('a1');
    expect(moveCardSelection(GRID, 'a1', 'down')).toBe('a2');
    expect(moveCardSelection(GRID, 'a2', 'down')).toBe('a3');
    expect(moveCardSelection(GRID, 'a3', 'down')).toBe('a3');
    expect(moveCardSelection(GRID, 'a3', 'up')).toBe('a2');
  });

  it('→ 跨列落于目标列纵向最近行;目标列卡数更少取末卡', () => {
    // A 行0 → C 行0(跳过空列 B)。
    expect(moveCardSelection(GRID, 'a1', 'right')).toBe('c1');
    // A 行2 → C 行1(末卡)。
    expect(moveCardSelection(GRID, 'a3', 'right')).toBe('c2');
    // C 行1 → A 行1(反向同规则)。
    expect(moveCardSelection(GRID, 'c2', 'left')).toBe('a2');
  });

  it('空列穿透:目标列为空 → 下一非空列', () => {
    // a1 右移:B 空 → 穿透至 C。
    expect(moveCardSelection(GRID, 'a1', 'right')).toBe('c1');
    // c1 左移:B 空 → 穿透至 A。
    expect(moveCardSelection(GRID, 'c1', 'left')).toBe('a1');
  });

  it('该方向无非空列 → 保持原选中', () => {
    expect(moveCardSelection(GRID, 'c2', 'right')).toBe('c2');
    expect(moveCardSelection(GRID, 'a1', 'left')).toBe('a1');
  });

  it('全部列为空 → null(保持原选中并忽略移动键)', () => {
    const emptyGrid = buildBoardGrid([column('X'), column('Y')], { X: [], Y: [] });
    expect(moveCardSelection(emptyGrid, 'whatever', 'down')).toBeNull();
    expect(moveCardSelection(emptyGrid, null, 'right')).toBeNull();
  });

  it('选中卡已不存在(实时移除)→ 重置于首个非空列首卡', () => {
    expect(moveCardSelection(GRID, 'gone', 'down')).toBe('a1');
  });

  it('折叠列不参与键盘遍历', () => {
    const grid = buildBoardGrid([column('A'), column('B', true), column('C')], CARDS_BY_KEY);
    // a1 右移:折叠的 B 不在网格中 → 直接落 C。
    expect(moveCardSelection(grid, 'a1', 'right')).toBe('c1');
  });
});

describe('columnKeyOfCard / nextColumnKey', () => {
  it('定位选中卡所在列', () => {
    expect(columnKeyOfCard(GRID, 'a2')).toBe('A');
    expect(columnKeyOfCard(GRID, 'c1')).toBe('C');
    expect(columnKeyOfCard(GRID, 'missing')).toBeNull();
  });

  it('下一列循环(S 改状态键盘路径)', () => {
    expect(nextColumnKey(GRID, 'A')).toBe('B');
    expect(nextColumnKey(GRID, 'C')).toBe('A');
    expect(nextColumnKey(GRID, 'missing')).toBeNull();
  });
});
