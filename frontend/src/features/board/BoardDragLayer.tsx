/**
 * 拖拽浮层(portal 渲染,design-quality §9.4)。
 *
 * 仅渲染浮动副本:fixed 定位、pointer-events none、跟随指针 transform,
 * 阴影 token(--shadow-2)+ 轻微缩放(0.97);prefers-reduced-motion 禁用过渡。
 * 落点指示线与 WIP 预检条在目标列内联渲染(BoardColumns),便于定位与测试。
 * z-index 使用 var(--z-overlay)。
 */
import { createPortal } from 'react-dom';
import type { DragState } from './useBoardDrag';
import './board-drag.css';

interface BoardDragLayerProps {
  readonly dragState: DragState;
}

export function BoardDragLayer({ dragState }: BoardDragLayerProps): React.JSX.Element {
  // 父级(BoardColumns)仅在 dragState 非空时渲染本组件,故此处无需空判。
  const { sourceRect, pointerX, pointerY, cardIdentifier, returning } = dragState;
  const offsetX = sourceRect.width / 2;
  const offsetY = sourceRect.height / 2;
  // §9.4.4 回位动画:returning 时浮层经 CSS 过渡滑回源卡位置并恢复原始尺寸。
  const transform =
    returning === true
      ? `translate(${sourceRect.left}px, ${sourceRect.top}px) scale(1)`
      : `translate(${pointerX - offsetX}px, ${pointerY - offsetY}px) scale(0.97)`;
  const cloneClass =
    returning === true ? 'mesh-board-drag__clone mesh-board-drag__clone--returning' : 'mesh-board-drag__clone';
  return createPortal(
    <div className="mesh-board-drag__layer" aria-hidden="true">
      <div
        className={cloneClass}
        data-testid="board-drag-clone"
        style={{
          width: `${sourceRect.width}px`,
          transform,
        }}
      >
        <span className="mesh-board__card-id">{cardIdentifier}</span>
      </div>
    </div>,
    document.body,
  );
}
