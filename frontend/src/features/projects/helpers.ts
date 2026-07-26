/**
 * 项目前缀 key 辅助(§5.1:^[A-Z][A-Z0-9_]{1,11}$,2–12 字符,首字符为字母)。
 * 纯函数,供 CreateProjectDialog 客户端即时校验与自动建议复用。
 */

export const PROJECT_KEY_PATTERN = /^[A-Z][A-Z0-9_]{1,11}$/;

const PROJECT_KEY_MAX_LENGTH = 12;
const NON_KEY_CHAR = /[^A-Z0-9_]/g;
const LEADING_NON_LETTER = /^[^A-Z]+/;

export function isValidProjectKey(key: string): boolean {
  return PROJECT_KEY_PATTERN.test(key);
}

/**
 * 由项目名自动建议 key(§4.3):大写化,非字母数字 → `_`,首字符须为字母,
 * 截断至 12 字符。无可用字母字符时返回空串(交由用户手填)。
 */
export function suggestProjectKey(name: string): string {
  const upper = name.toUpperCase();
  const replaced = upper.replace(NON_KEY_CHAR, '_');
  const stripped = replaced.replace(LEADING_NON_LETTER, '');
  return stripped.slice(0, PROJECT_KEY_MAX_LENGTH);
}
