/**
 * 指针拖拽测试工具(jsdom 无 PointerEvent / 无真实布局)。
 *
 * 模拟手段说明:
 * - jsdom 未实现 PointerEvent,这里以继承 MouseEvent 的桩补齐(clientX/clientY 经
 *   MouseEventInit 透传;pointerType/pointerId 自定义赋值),经 vi.stubGlobal 注入;
 * - jsdom 的 getBoundingClientRect 恒返回 0,这里逐元素覆盖为给定矩形,命中检测
 *   (dragGeometry)据此工作;
 * - document 级 pointermove/pointerup 经 fireEvent 直接在 document 上派发触达监听。
 */
import { vi } from 'vitest';

interface RectInit {
  readonly left?: number;
  readonly top?: number;
  readonly right?: number;
  readonly bottom?: number;
}

/** 注入 PointerEvent 桩(幂等:已存在则跳过)。 */
export function ensurePointerEvent(): void {
  if (typeof window.PointerEvent !== 'undefined') return;
  class PointerEventStub extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    constructor(type: string, params: PointerEventInit & { pointerType?: string } = {}) {
      super(type, params);
      this.pointerId = params.pointerId ?? 0;
      this.pointerType = params.pointerType ?? 'mouse';
    }
  }
  vi.stubGlobal('PointerEvent', PointerEventStub);
}

/** 覆盖元素 getBoundingClientRect 为给定矩形(其余字段归零)。 */
export function mockRect(element: HTMLElement, rect: RectInit): void {
  const full: DOMRect = {
    left: rect.left ?? 0,
    top: rect.top ?? 0,
    right: rect.right ?? 0,
    bottom: rect.bottom ?? 0,
    width: (rect.right ?? 0) - (rect.left ?? 0),
    height: (rect.bottom ?? 0) - (rect.top ?? 0),
    x: rect.left ?? 0,
    y: rect.top ?? 0,
    toJSON: () => ({}),
  } as DOMRect;
  element.getBoundingClientRect = () => full;
}
