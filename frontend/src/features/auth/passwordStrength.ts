/**
 * 密码强度评估(纯逻辑;auth.md §4.1 强度条 + 实时校验,§5.1 口令策略)。
 *
 * 客户端规则与后端 `validate_password_strength`(backend security.py)一致——
 * ≥8 位、同时含字母与数字、非常见弱口令——仅作即时反馈(实时校验);服务端
 * 始终是权威(含更大泄露库比对),故提交不因客户端评估禁用。常见弱口令表为
 * 后端 `COMMON_WEAK_PASSWORDS` 的镜像副本,改一处须同步另一处。
 */

export const PASSWORD_MIN_LENGTH = 8;

/** 强口令的长度加分线:满足全部规则且 ≥12 位记满分(strong) */
const STRONG_LENGTH = 12;

/** 后端 COMMON_WEAK_PASSWORDS 的客户端镜像(小写;即时反馈用,非权威) */
export const COMMON_WEAK_PASSWORDS: ReadonlySet<string> = new Set([
  'password',
  'password1',
  'password123',
  '123456',
  '12345678',
  '123456789',
  '1234567890',
  'qwerty',
  'qwerty123',
  'abc123',
  '111111',
  'letmein',
  'welcome',
  'iloveyou',
  'admin123',
  'mesh1234',
]);

const HAS_LETTER = /[A-Za-z]/;
const HAS_DIGIT = /[0-9]/;

/** 口令策略规则(与后端 details.reason 一一对应) */
export type PasswordRule = 'length' | 'letterAndDigit' | 'notCommon';

export interface PasswordAssessment {
  /** 强度分 0–4(空串 = 0;满足全部规则 = 3,再满足加长 = 4) */
  score: number;
  /** 未满足的规则(空 = 全部满足;空串输入亦为空,不打扰未输入用户) */
  failedRules: readonly PasswordRule[];
  /** 非空且全部规则满足 */
  isValid: boolean;
}

/** 评估口令强度(确定性、无副作用;渲染期直接调用)。 */
export function assessPasswordStrength(password: string): PasswordAssessment {
  if (password.length === 0) {
    return { score: 0, failedRules: [], isValid: false };
  }
  const failedRules: PasswordRule[] = [];
  const longEnough = password.length >= PASSWORD_MIN_LENGTH;
  const hasLetterAndDigit = HAS_LETTER.test(password) && HAS_DIGIT.test(password);
  const notCommon = !COMMON_WEAK_PASSWORDS.has(password.toLowerCase());
  if (!longEnough) failedRules.push('length');
  if (!hasLetterAndDigit) failedRules.push('letterAndDigit');
  if (!notCommon) failedRules.push('notCommon');
  const passed = 3 - failedRules.length;
  // 命中常见弱口令或长度不达标时压到 weak(短/常见口令其余规则再好不得高于 1)。
  const score =
    !notCommon || !longEnough ? 1 : passed === 3 && password.length >= STRONG_LENGTH ? 4 : passed;
  return { score, failedRules, isValid: failedRules.length === 0 };
}
