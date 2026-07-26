import { describe, expect, it } from 'vitest';
import {
  COMMON_WEAK_PASSWORDS,
  PASSWORD_MIN_LENGTH,
  assessPasswordStrength,
} from '../passwordStrength';

describe('assessPasswordStrength(auth.md §4.1/§5.1,镜像后端口令策略)', () => {
  it('空串:score 0、无失败规则、isValid false(不打扰未输入用户)', () => {
    expect(assessPasswordStrength('')).toEqual({ score: 0, failedRules: [], isValid: false });
  });

  it('过短 → length 规则失败', () => {
    const result = assessPasswordStrength('a1');
    expect(result.failedRules).toContain('length');
    expect(result.isValid).toBe(false);
  });

  it('仅字母/仅数字 → letterAndDigit 规则失败', () => {
    expect(assessPasswordStrength('abcdefgh').failedRules).toContain('letterAndDigit');
    expect(assessPasswordStrength('12345678').failedRules).toContain('letterAndDigit');
  });

  it('命中常见弱口令(大小写不敏感)→ notCommon 失败且压到 weak', () => {
    const lower = assessPasswordStrength('password123');
    expect(lower.failedRules).toEqual(['notCommon']);
    expect(lower.score).toBe(1);
    expect(lower.isValid).toBe(false);
    const mixedCase = assessPasswordStrength('Password123');
    expect(mixedCase.failedRules).toEqual(['notCommon']);
    expect(mixedCase.score).toBe(1);
  });

  it('满足全部规则:good(8–11 位)与 strong(≥12 位)分级', () => {
    const good = assessPasswordStrength('Tr5x9qLm2v');
    expect(good.failedRules).toEqual([]);
    expect(good.isValid).toBe(true);
    expect(good.score).toBe(3);

    const strong = assessPasswordStrength('Tr5x9qLm2vBz');
    expect(strong.failedRules).toEqual([]);
    expect(strong.score).toBe(4);
  });

  it('多条规则同时失败', () => {
    const result = assessPasswordStrength('abc');
    expect(result.failedRules).toEqual(['length', 'letterAndDigit']);
    expect(result.score).toBe(1);
  });

  it('常见弱口令表与后端常量一致(最小长度 8)', () => {
    expect(PASSWORD_MIN_LENGTH).toBe(8);
    for (const sample of ['password', 'qwerty123', 'mesh1234']) {
      expect(COMMON_WEAK_PASSWORDS.has(sample)).toBe(true);
    }
  });
});
