import { describe, expect, it } from 'vitest';
import {
  WCAG_AA_LARGE_RATIO,
  WCAG_AA_RATIO,
  compositeOver,
  contrastRatio,
  hexToRgb,
  meetsAA,
  parseColor,
  relativeLuminance,
} from '../contrast';

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
    const invalid = [
      '',
      'red',
      '#12',
      '#1234',
      '#12345',
      '#1234567',
      '123456',
      '#gggggg',
      '#fffffg',
      'rgb(1,2,3)',
    ];
    for (const value of invalid) {
      expect(() => hexToRgb(value), `expected throw for ${JSON.stringify(value)}`).toThrow(
        /invalid hex/i,
      );
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

describe('parseColor', () => {
  it('解析 #rrggbb 六位格式(alpha 缺省 1)', () => {
    // Arrange / Act
    const parsed = parseColor('#1d4ed8');

    // Assert
    expect(parsed).toEqual({ r: 29, g: 78, b: 216, a: 1 });
  });

  it('解析 #rgb 三位简写', () => {
    expect(parseColor('#fff')).toEqual({ r: 255, g: 255, b: 255, a: 1 });
    expect(parseColor('#f0a')).toEqual({ r: 255, g: 0, b: 170, a: 1 });
  });

  it('解析 rgba(15,23,42,0.45) 取 alpha 通道', () => {
    expect(parseColor('rgba(15, 23, 42, 0.45)')).toEqual({ r: 15, g: 23, b: 42, a: 0.45 });
    expect(parseColor('rgba(0,0,0,0.6)')).toEqual({ r: 0, g: 0, b: 0, a: 0.6 });
  });

  it('解析 rgb() 无 alpha 段时 a = 1', () => {
    expect(parseColor('rgb(255,255,255)')).toEqual({ r: 255, g: 255, b: 255, a: 1 });
    expect(parseColor('rgb( 1 , 2 , 3 )')).toEqual({ r: 1, g: 2, b: 3, a: 1 });
  });

  it('非法输入返回 null(不抛错)', () => {
    const invalid = [
      '',
      'red',
      '#12',
      '#12345',
      '#gggggg',
      'hsl(0, 0%, 0%)',
      'rgb(1, 2)',
      'rgb(300, 0, 0)',
      'rgba(0, 0, 0, 1.5)',
      'oklch(0.5 0.1 200)',
    ];
    for (const value of invalid) {
      expect(parseColor(value), `expected null for ${JSON.stringify(value)}`).toBeNull();
    }
  });
});

describe('compositeOver(alpha 合成 out = fg*a + bg*(1-a))', () => {
  it('半透明前景按公式合成到不透明底色', () => {
    // Arrange
    const fg = { r: 15, g: 23, b: 42, a: 0.45 };

    // Act
    const result = compositeOver(fg, '#ffffff');

    // Assert
    expect(result).toEqual({
      r: Math.round(15 * 0.45 + 255 * 0.55),
      g: Math.round(23 * 0.45 + 255 * 0.55),
      b: Math.round(42 * 0.45 + 255 * 0.55),
    });
  });

  it('不透明前景(a = 1)合成结果即前景自身通道', () => {
    expect(compositeOver({ r: 1, g: 2, b: 3, a: 1 }, '#ffffff')).toEqual({ r: 1, g: 2, b: 3 });
  });

  it('全透明前景(a = 0)合成结果即底色', () => {
    expect(compositeOver({ r: 1, g: 2, b: 3, a: 0 }, '#111827')).toEqual({ r: 17, g: 24, b: 39 });
  });
});

describe('contrastRatio(含 alpha 语义)', () => {
  it('前景含 alpha 时先对底色合成再计算亮度', () => {
    // Arrange: scrim 式半透明值对白色底
    const ratio = contrastRatio('rgba(15, 23, 42, 0.45)', '#ffffff');

    // Assert: 与合成后 rgb(147, 151, 159) 对白的比值一致
    const composited = compositeOver({ r: 15, g: 23, b: 42, a: 0.45 }, '#ffffff');
    const luminance = relativeLuminance(composited);
    expect(ratio).toBeCloseTo((1 + 0.05) / (luminance + 0.05), 6);
  });

  it('非法颜色输入抛错(保持既有契约)', () => {
    expect(() => contrastRatio('not-a-color', '#ffffff')).toThrow(/invalid color/i);
    expect(() => contrastRatio('#000000', 'transparent')).toThrow(/invalid color/i);
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

  it('large-text / graphic 组阈值为 3.0(#949494/白 ≈ 3.03:过 3.0、不过 4.5)', () => {
    expect(meetsAA('#949494', '#ffffff', 'large-text')).toBe(true);
    expect(meetsAA('#949494', '#ffffff', 'graphic')).toBe(true);
    expect(meetsAA('#949494', '#ffffff')).toBe(false);
  });

  it('前景含 alpha 时对底色合成后判定', () => {
    // 近乎全透明的白色文字叠在白底上 → 合成后约等于白 → 不达标
    expect(meetsAA('rgba(255, 255, 255, 0.1)', '#ffffff')).toBe(false);
  });

  it('阈值常量:text = 4.5,large-text/graphic = 3.0', () => {
    expect(WCAG_AA_RATIO).toBe(4.5);
    expect(WCAG_AA_LARGE_RATIO).toBe(3.0);
  });
});
