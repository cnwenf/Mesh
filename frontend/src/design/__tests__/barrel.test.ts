import { describe, expect, it } from 'vitest';
import * as design from '../index';

describe('design 桶导出', () => {
  it('暴露主题/对比度/token 与全部组件 API', () => {
    expect(design.ThemeProvider).toBeTypeOf('function');
    expect(design.resolveTheme).toBeTypeOf('function');
    expect(design.contrastRatio).toBeTypeOf('function');
    expect(design.hexToRgb).toBeTypeOf('function');
    expect(design.meetsAA).toBeTypeOf('function');
    expect(design.relativeLuminance).toBeTypeOf('function');
    expect(design.LIGHT_TOKENS).toBeTypeOf('object');
    expect(design.DARK_TOKENS).toBeTypeOf('object');
    expect(design.AA_CONTRAST_PAIRS.length).toBeGreaterThan(0);
    expect(design.Button).toBeTruthy();
    expect(design.IconButton).toBeTruthy();
    expect(design.Input).toBeTruthy();
    expect(design.Select).toBeTruthy();
    expect(design.Skeleton).toBeTypeOf('function');
    expect(design.EmptyState).toBeTypeOf('function');
    expect(design.ErrorState).toBeTypeOf('function');
    expect(design.Banner).toBeTypeOf('function');
    expect(design.ToastProvider).toBeTypeOf('function');
    expect(design.useToast).toBeTypeOf('function');
    expect(design.DEFAULT_TOAST_DURATION_MS).toBe(5000);
    expect(design.Dialog).toBeTypeOf('function');
    expect(design.Kbd).toBeTypeOf('function');
    expect(design.StatusDot).toBeTypeOf('function');
  });
});
