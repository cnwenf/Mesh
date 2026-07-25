import { describe, expect, it } from 'vitest';
import { WCAG_AA_RATIO, contrastRatio, hexToRgb, meetsAA, relativeLuminance } from '../contrast';

describe('hexToRgb', () => {
  it('解析 #rrggbb 六位格式', () => {
    expect(hexToRgb('#1d4ed8')).toEqual({ r: 29, g: 78, b: 216 });
    expect(hexToRgb('#000000')).toEqual({ r: 0, g: 0, b: 0 });
    expect(hexToRgb('#ffffff')).toEqual({ r: 255, g: 255, b: 255 });
  });

  it('解析 #rgb 三位简写(每位翻倍)', () => {
    expect(hexToRgb('#fff')).toEqual({ r: 255, g: 255, b: 255 });
    expect(hexToRgb('#000')).toEqual({ r: 0, g: 0, b: 0 });
    expect(hexToRgb('#f0a')).toEqual({ r: 255, g: 0, b: 170 });
  });

  it('大小写不敏感', () => {
    expect(hexToRgb('#ABCDEF')).toEqual({ r: 0xab, g: 0xcd, b: 0xef });
    expect(hexToRgb('#AbC')).toEqual({ r: 0xaa, g: 0xbb, b: 0xcc });
  });

  it('非法输入抛错', () => {
    const invalid = ['', 'red', '#12', '#1234', '#12345', '#1234567', '123456', '#gggggg', '#fffffg', 'rgb(1,2,3)'];
    for (const value of invalid) {
      expect(() => hexToRgb(value), `expected throw for ${JSON.stringify(value)}`).toThrow(/invalid hex/i);
    }
  });
});

describe('relativeLuminance(WCAG 2.1 公式)', () => {
  it('黑色 = 0,白色 = 1', () => {
    expect(relativeLuminance({ r: 0, g: 0, b: 0 })).toBeCloseTo(0, 5);
    expect(relativeLuminance({ r: 255, g: 255, b: 255 })).toBeCloseTo(1, 5);
  });

  it('纯红 = 0.2126(系数已知值)', () => {
    expect(relativeLuminance({ r: 255, g: 0, b: 0 })).toBeCloseTo(0.2126, 4);
  });

  it('低通道走线性分段(srgb ≤ 0.04045)', () => {
    // 通道 10/255 ≈ 0.0392 < 0.04045 → c/12.92 分段
    const luminance = relativeLuminance({ r: 10, g: 10, b: 10 });
    const linear = 10 / 255 / 12.92;
    expect(luminance).toBeCloseTo(linear, 6);
  });
});

describe('contrastRatio', () => {
  it('黑/白 = 21(最大),且与参数顺序无关', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 2);
    expect(contrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 2);
  });

  it('同色 = 1', () => {
    expect(contrastRatio('#1d4ed8', '#1d4ed8')).toBeCloseTo(1, 5);
  });

  it('经典边界:#767676/白 ≈ 4.54(过),#777777/白 ≈ 4.48(不过)', () => {
    expect(contrastRatio('#767676', '#ffffff')).toBeGreaterThan(4.5);
    expect(contrastRatio('#777777', '#ffffff')).toBeLessThan(4.5);
  });
});

describe('meetsAA', () => {
  it('≥ 4.5:1 → true', () => {
    expect(meetsAA('#767676', '#ffffff')).toBe(true);
    expect(meetsAA('#000000', '#ffffff')).toBe(true);
  });

  it('< 4.5:1 → false', () => {
    expect(meetsAA('#777777', '#ffffff')).toBe(false);
    expect(meetsAA('#ffffff', '#ffffff')).toBe(false);
  });

  it('阈值为 WCAG_AA_RATIO = 4.5', () => {
    expect(WCAG_AA_RATIO).toBe(4.5);
  });
});
