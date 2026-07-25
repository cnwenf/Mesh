/**
 * 项目前缀 key 辅助纯函数测试(project.md §5.1:^[A-Z][A-Z0-9_]{1,11}$)。
 * 覆盖校验与自动建议的正常路径与边界(过短/过长/小写/非法首字符/非法字符/空)。
 */
import { describe, expect, it } from 'vitest';
import { PROJECT_KEY_PATTERN, isValidProjectKey, suggestProjectKey } from '../helpers';

describe('isValidProjectKey', () => {
  it('接受字母开头、2–12 位、仅大写字母/数字/下划线的 key', () => {
    expect(isValidProjectKey('AB')).toBe(true);
    expect(isValidProjectKey('A1')).toBe(true);
    expect(isValidProjectKey('A_')).toBe(true);
    expect(isValidProjectKey('APOLLO')).toBe(true);
    expect(isValidProjectKey('WEB_2_0')).toBe(true);
  });

  it('接受恰好 12 位的 key', () => {
    expect(isValidProjectKey('ABCDEFGHIJKL')).toBe(true);
  });

  it('拒绝单个字符(少于 2 位)', () => {
    expect(isValidProjectKey('A')).toBe(false);
  });

  it('拒绝超过 12 位的 key', () => {
    expect(isValidProjectKey('ABCDEFGHIJKLM')).toBe(false);
  });

  it('拒绝小写', () => {
    expect(isValidProjectKey('ab')).toBe(false);
    expect(isValidProjectKey('Apollo')).toBe(false);
  });

  it('拒绝以数字或下划线开头', () => {
    expect(isValidProjectKey('1A')).toBe(false);
    expect(isValidProjectKey('_A')).toBe(false);
  });

  it('拒绝包含空格或特殊字符', () => {
    expect(isValidProjectKey('A B')).toBe(false);
    expect(isValidProjectKey('A-B')).toBe(false);
    expect(isValidProjectKey('A.B')).toBe(false);
  });

  it('拒绝空串', () => {
    expect(isValidProjectKey('')).toBe(false);
  });

  it('PROJECT_KEY_PATTERN 与 isValidProjectKey 判定一致', () => {
    expect(PROJECT_KEY_PATTERN.test('APL')).toBe(true);
    expect(PROJECT_KEY_PATTERN.test('apl')).toBe(false);
  });
});

describe('suggestProjectKey', () => {
  it('大写化普通名称', () => {
    expect(suggestProjectKey('Apollo')).toBe('APOLLO');
  });

  it('将非字母数字字符替换为下划线', () => {
    expect(suggestProjectKey('my project')).toBe('MY_PROJECT');
    expect(suggestProjectKey('web-app')).toBe('WEB_APP');
    expect(suggestProjectKey('web 2.0')).toBe('WEB_2_0');
  });

  it('剥离前导非字母字符(数字/下划线开头)', () => {
    expect(suggestProjectKey('123abc')).toBe('ABC');
    expect(suggestProjectKey('___xyz')).toBe('XYZ');
    expect(suggestProjectKey('2026 Roadmap')).toBe('ROADMAP');
  });

  it('截断至 12 字符', () => {
    expect(suggestProjectKey('abcdefghijklmnop')).toBe('ABCDEFGHIJKL');
  });

  it('无可用字母字符时返回空串', () => {
    expect(suggestProjectKey('123')).toBe('');
    expect(suggestProjectKey('!!!')).toBe('');
    expect(suggestProjectKey('')).toBe('');
  });
});
