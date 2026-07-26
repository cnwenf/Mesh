/**
 * 密码强度条 + 实时校验(auth.md §4.1:注册页/重置页共用)。
 *
 * 随输入即时评估(assessPasswordStrength):四段强度条 + 文字档位(不以颜色
 * 单独表意)+ 未满足规则提示(aria-live 实时播报)。纯即时反馈,不禁用提交——
 * 服务端为口令策略权威(§5.1;含客户端无法覆盖的更大泄露库比对)。
 * 空串不渲染(不打扰尚未输入的用户)。
 */
import { useT } from '../../i18n';
import { assessPasswordStrength } from './passwordStrength';
import type { PasswordRule } from './passwordStrength';

export interface PasswordStrengthMeterProps {
  password: string;
}

/** 规则 → i18n 键(实时校验提示文案) */
const RULE_KEYS: Readonly<Record<PasswordRule, string>> = {
  length: 'auth.ruleLength',
  letterAndDigit: 'auth.ruleLetterAndDigit',
  notCommon: 'auth.ruleNotCommon',
};

/** 强度分 0–4 → 档位文案键(0/1 均为 weak) */
const LEVEL_KEYS = [
  'auth.strengthWeak',
  'auth.strengthWeak',
  'auth.strengthFair',
  'auth.strengthGood',
  'auth.strengthStrong',
] as const;

const SEGMENT_COUNT = 4;

export function PasswordStrengthMeter(props: PasswordStrengthMeterProps): React.JSX.Element | null {
  const { password } = props;
  const t = useT();
  if (password.length === 0) {
    return null;
  }
  const { score, failedRules } = assessPasswordStrength(password);
  const levelKey = LEVEL_KEYS[score];
  return (
    <div className="mesh-password-strength">
      <div
        role="meter"
        data-testid="password-strength"
        data-score={String(score)}
        aria-label={t('auth.passwordStrength')}
        aria-valuemin={0}
        aria-valuemax={4}
        aria-valuenow={score}
        className="mesh-password-strength__bar"
      >
        {Array.from({ length: SEGMENT_COUNT }, (_, segment) => (
          <span
            key={segment}
            className={segment < score ? 'mesh-password-strength__segment is-filled' : 'mesh-password-strength__segment'}
          />
        ))}
      </div>
      <p className="mesh-password-strength__label" data-testid="password-strength-label">
        {t('auth.passwordStrength')}: {t(levelKey)}
      </p>
      {failedRules.length > 0 ? (
        <ul aria-live="polite" className="mesh-password-strength__rules" data-testid="password-rules">
          {failedRules.map((rule) => (
            <li key={rule}>{t(RULE_KEYS[rule])}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
