/**
 * pinchMath 单测(parity §2.22 灯箱手势缩放):距离/钳制/捏合比例/双击切换。
 */
import { describe, expect, it } from 'vitest';
import {
  clampScale,
  doubleTapScale,
  LIGHTBOX_MAX_SCALE,
  LIGHTBOX_MIN_SCALE,
  pinchScale,
  pointerDistance,
} from '../pinchMath';

describe('pointerDistance', () => {
  it('computes the Euclidean distance between two points', () => {
    expect(pointerDistance({ x: 0, y: 0 }, { x: 3, y: 4 })).toBe(5);
    expect(pointerDistance({ x: 2, y: 2 }, { x: 2, y: 2 })).toBe(0);
  });
});

describe('clampScale', () => {
  it('clamps within [min, max]', () => {
    expect(clampScale(2, LIGHTBOX_MIN_SCALE, LIGHTBOX_MAX_SCALE)).toBe(2);
    expect(clampScale(10, LIGHTBOX_MIN_SCALE, LIGHTBOX_MAX_SCALE)).toBe(LIGHTBOX_MAX_SCALE);
    expect(clampScale(0.1, LIGHTBOX_MIN_SCALE, LIGHTBOX_MAX_SCALE)).toBe(LIGHTBOX_MIN_SCALE);
  });

  it('falls back to min for non-finite input', () => {
    expect(clampScale(Number.NaN, LIGHTBOX_MIN_SCALE, LIGHTBOX_MAX_SCALE)).toBe(LIGHTBOX_MIN_SCALE);
  });
});

describe('pinchScale', () => {
  it('scales by the distance ratio against the base scale', () => {
    // 距离翻倍 → 缩放翻倍
    expect(pinchScale(100, 200, 1, LIGHTBOX_MIN_SCALE, LIGHTBOX_MAX_SCALE)).toBe(2);
    // 距离减半 → 缩放减半
    expect(pinchScale(100, 50, 2, LIGHTBOX_MIN_SCALE, LIGHTBOX_MAX_SCALE)).toBe(1);
  });

  it('clamps the result to the allowed range', () => {
    expect(pinchScale(10, 1000, 1, LIGHTBOX_MIN_SCALE, LIGHTBOX_MAX_SCALE)).toBe(LIGHTBOX_MAX_SCALE);
    expect(pinchScale(1000, 10, 1, LIGHTBOX_MIN_SCALE, LIGHTBOX_MAX_SCALE)).toBe(LIGHTBOX_MIN_SCALE);
  });

  it('ignores a non-positive start distance (no division by zero)', () => {
    expect(pinchScale(0, 100, 1.5, LIGHTBOX_MIN_SCALE, LIGHTBOX_MAX_SCALE)).toBe(1.5);
    expect(pinchScale(-1, 100, 9, LIGHTBOX_MIN_SCALE, LIGHTBOX_MAX_SCALE)).toBe(LIGHTBOX_MAX_SCALE);
  });
});

describe('doubleTapScale', () => {
  it('toggles between 1× and 2× around the threshold', () => {
    expect(doubleTapScale(1)).toBe(2);
    expect(doubleTapScale(1.4)).toBe(2);
    expect(doubleTapScale(2)).toBe(1);
    expect(doubleTapScale(3.5)).toBe(1);
  });
});
