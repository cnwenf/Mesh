/**
 * formatFileSize 单测(M2:人性化文件大小,不裸渲染字节数)。
 */
import { describe, expect, it } from 'vitest';
import { formatFileSize } from '../format';

describe('formatFileSize', () => {
  it('renders bytes without decimals', () => {
    expect(formatFileSize(0)).toBe('0 B');
    expect(formatFileSize(500)).toBe('500 B');
  });

  it('guards against negative and non-finite input', () => {
    expect(formatFileSize(-5)).toBe('0 B');
    expect(formatFileSize(Number.NaN)).toBe('0 B');
    expect(formatFileSize(Number.POSITIVE_INFINITY)).toBe('0 B');
  });

  it('uses one decimal under 100 of a unit', () => {
    expect(formatFileSize(1536)).toBe('1.5 KB');
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5.0 MB');
  });

  it('drops decimals at 100+ of a unit', () => {
    expect(formatFileSize(153600)).toBe('150 KB');
  });

  it('caps at the largest unit (TB)', () => {
    expect(formatFileSize(2 * 1024 ** 4)).toBe('2.0 TB');
  });
});
