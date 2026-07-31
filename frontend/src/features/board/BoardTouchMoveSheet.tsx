/**
 * 触摸移动底部 sheet(design-quality §8.3)。
 *
 * 长按 350ms(coarse pointer)→ 打开 Drawer(≤599px 自动转底部 sheet),
 * 列出目标列(计数 + WIP 状态);block 已满列禁用并附原因;
 * 选择列 → onDropCard(末尾位置);sheet 内含列内排序操作
 * (顶/底/上移/下移,§8.3 卡片在当前列内排序)。
 */
import { useCallback } from 'react';
import { Drawer, Icon } from '../../design';
import { useT } from '../../i18n';
import type { BoardCard } from './projection';
import type { BoardColumn } from './types';
import './board-drag.css';

interface BoardTouchMoveSheetProps {
  /** 非空:父级仅在选中卡片时渲染本组件(避免组件内空判分支)。 */
  readonly card: BoardCard;
  readonly columns: readonly BoardColumn[];
  readonly cardsByKey: Readonly<Record<string, readonly BoardCard[]>>;
  readonly onDropCard: (issueId: string, toGroupKey: string, position: number) => void;
  /** 计算落点位置(列 key + 插入 index,null = 列底)。 */
  readonly computePosition: (columnKey: string, index: number | null) => number;
  readonly onClose: () => void;
  readonly announce: (message: string) => void;
  readonly getColumnLabel: (columnKey: string) => string;
}

/** WIP block 已满判断(与拖拽预检一致)。 */
function isColumnBlocked(column: BoardColumn): boolean {
  return (
    column.wip !== null &&
    column.wip.enforcement === 'block' &&
    column.count >= column.wip.limit
  );
}

export function BoardTouchMoveSheet(props: BoardTouchMoveSheetProps): React.JSX.Element {
  const { card, columns, cardsByKey, onDropCard, computePosition, onClose, announce, getColumnLabel } = props;
  const t = useT();

  const handleSelectColumn = useCallback(
    (columnKey: string) => {
      const position = computePosition(columnKey, null);
      onDropCard(card.id, columnKey, position);
      announce(t('board.touchMoved', { identifier: card.identifier, column: getColumnLabel(columnKey) }));
      onClose();
    },
    [card, computePosition, onDropCard, onClose, announce, getColumnLabel, t],
  );

  /** 列内排序:顶/底/上移/下移(§8.3)。 */
  const handleReorder = useCallback(
    (direction: 'top' | 'bottom' | 'up' | 'down') => {
      // 一次遍历定位卡片所在列及其列内 index(合并查找,避免分离的空判分支)。
      let sourceKey = '';
      let sourceCards: readonly BoardCard[] | null = null;
      let currentIndex = -1;
      for (const col of columns) {
        const list = cardsByKey[col.key] ?? [];
        const index = list.findIndex((item) => item.id === card.id);
        if (index !== -1) {
          sourceKey = col.key;
          sourceCards = list;
          currentIndex = index;
          break;
        }
      }
      if (sourceCards === null) return;

      let targetIndex: number | null;
      if (direction === 'top') targetIndex = 0;
      else if (direction === 'bottom') targetIndex = null;
      else if (direction === 'up') targetIndex = Math.max(0, currentIndex - 1);
      else targetIndex = Math.min(sourceCards.length - 1, currentIndex + 1);

      const position = computePosition(sourceKey, targetIndex);
      onDropCard(card.id, sourceKey, position);
      announce(t('board.reorderMoved', { identifier: card.identifier, direction: t('board.reorder.' + direction) }));
    },
    [card, columns, cardsByKey, computePosition, onDropCard, announce, t],
  );

  return (
    <Drawer
      open
      onClose={onClose}
      title={t('board.touchMoveTitle', { identifier: card.identifier })}
      closeLabel={t('common.close')}
    >
      <div className="mesh-board-touch" data-testid="board-touch-sheet">
        {/* 列内排序操作 */}
        <div className="mesh-board-touch__reorder" role="group" aria-label={t('board.reorderGroupLabel')}>
          <button type="button" className="mesh-board-touch__reorder-btn" data-testid="touch-move-top" onClick={() => handleReorder('top')}>
            <Icon name="arrow-up" size={16} /> {t('board.moveTop')}
          </button>
          <button type="button" className="mesh-board-touch__reorder-btn" data-testid="touch-move-up" onClick={() => handleReorder('up')}>
            <Icon name="chevron-up" size={16} /> {t('board.moveUp')}
          </button>
          <button type="button" className="mesh-board-touch__reorder-btn" data-testid="touch-move-down" onClick={() => handleReorder('down')}>
            <Icon name="chevron-down" size={16} /> {t('board.moveDown')}
          </button>
          <button type="button" className="mesh-board-touch__reorder-btn" data-testid="touch-move-bottom" onClick={() => handleReorder('bottom')}>
            <Icon name="arrow-down" size={16} /> {t('board.moveBottom')}
          </button>
        </div>

        {/* 目标列列表 */}
        <ul className="mesh-board-touch__columns" role="list" aria-label={t('board.targetColumnsLabel')}>
          {columns.map((column) => {
            const blocked = isColumnBlocked(column);
            const label = getColumnLabel(column.key);
            return (
              <li key={column.key}>
                <button
                  type="button"
                  className={`mesh-board-touch__column-btn ${blocked ? 'mesh-board-touch__column-btn--blocked' : ''}`.trim()}
                  data-testid={`touch-column-${column.key}`}
                  disabled={blocked}
                  onClick={() => handleSelectColumn(column.key)}
                >
                  <span className={`mesh-board__dot mesh-board__dot--${column.key}`} aria-hidden="true" />
                  <span className="mesh-board-touch__column-name">{label}</span>
                  <span className="mesh-board__count">{column.count}</span>
                  {column.wip !== null ? (
                    <span className="mesh-board-touch__wip">{column.count}/{column.wip.limit}</span>
                  ) : null}
                  {blocked ? (
                    <span className="mesh-board-touch__blocked-reason" data-testid={`touch-blocked-reason-${column.key}`}>
                      <Icon name="warning" size={16} /> {t('board.wipDragBlock')}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </Drawer>
  );
}
