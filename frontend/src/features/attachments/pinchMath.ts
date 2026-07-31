/**
 * 灯箱触控手势纯计算(attachment.md parity §2.22「手机端灯箱手势缩放」)。
 * 双指捏合缩放 + 双击切换的几何辅助:距离、比例钳制、双击档位。
 * 纯函数,无 DOM/React 依赖,便于单测。
 */

/** 指针坐标(客户端坐标,pointer 事件 clientX/clientY)。 */
export interface PointerPoint {
  readonly x: number;
  readonly y: number;
}

/** 灯箱缩放范围(与 Lightbox 既有 MIN/MAX 一致)。 */
export const LIGHTBOX_MIN_SCALE = 0.5;
export const LIGHTBOX_MAX_SCALE = 4;

/** 双击切换的两个档位:1× 与 2×。 */
export const DOUBLE_TAP_ZOOMED_SCALE = 2;
export const DOUBLE_TAP_BASE_SCALE = 1;
/** 判定「当前接近 1×」的阈值:< 此值视为未放大,双击放大;否则复位到 1×。 */
export const DOUBLE_TAP_THRESHOLD = 1.5;

/** 两指针欧氏距离。 */
export function pointerDistance(a: PointerPoint, b: PointerPoint): number {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return Math.hypot(dx, dy);
}

/** 把缩放值钳制到 [min, max];非有限值回退到 min。 */
export function clampScale(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

/**
 * 捏合缩放:以手势起始距离为基准,当前距离/起始距离 × 基准缩放,钳制到 [min, max]。
 * 起始距离 ≤0(单指/重合指针)时不做缩放,仅钳制基准值。
 */
export function pinchScale(
  startDistance: number,
  currentDistance: number,
  baseScale: number,
  min: number,
  max: number,
): number {
  if (startDistance <= 0) return clampScale(baseScale, min, max);
  return clampScale(baseScale * (currentDistance / startDistance), min, max);
}

/** 双击切换:接近 1×(低于阈值)→ 放大到 2×;否则复位到 1×。 */
export function doubleTapScale(current: number): number {
  return current < DOUBLE_TAP_THRESHOLD ? DOUBLE_TAP_ZOOMED_SCALE : DOUBLE_TAP_BASE_SCALE;
}
