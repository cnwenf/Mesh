import { describe, expect, it } from 'vitest';
import { VIEWPORT_BREAKPOINTS, viewportMode } from '../responsive';

describe('全局响应式模式边界(design-quality §8.1)', () => {
  it.each([
    [0, 'compact'],
    [320, 'compact'],
    [599, 'compact'],
    [600, 'medium'],
    [768, 'medium'],
    [1023, 'medium'],
    [1024, 'wide'],
    [1439, 'wide'],
    [1440, 'xwide'],
    [4096, 'xwide'],
  ] as const)('%ipx → %s', (width, expected) => {
    expect(viewportMode(width)).toBe(expected);
  });

  it('单一事实源恰好覆盖 compact/medium/wide/xwide 且边界连续', () => {
    expect(VIEWPORT_BREAKPOINTS).toEqual({
      compact: { min: 0, max: 599 },
      medium: { min: 600, max: 1023 },
      wide: { min: 1024, max: 1439 },
      xwide: { min: 1440 },
    });
    expect(VIEWPORT_BREAKPOINTS.compact.max + 1).toBe(VIEWPORT_BREAKPOINTS.medium.min);
    expect(VIEWPORT_BREAKPOINTS.medium.max + 1).toBe(VIEWPORT_BREAKPOINTS.wide.min);
    expect(VIEWPORT_BREAKPOINTS.wide.max + 1).toBe(VIEWPORT_BREAKPOINTS.xwide.min);
  });

  it.each([-1, Number.NaN, Number.POSITIVE_INFINITY])('拒绝非法宽度 %s', (width) => {
    expect(() => viewportMode(width)).toThrow(RangeError);
  });
});
