/**
 * 步骤键枚举守卫测试(onboarding.md §1.2.1 五步固定序 / §3.3 step_key 严格枚举)。
 */
import { describe, expect, it } from 'vitest';
import { STEP_KEYS, isOnboardingStepKey } from '../types';

describe('STEP_KEYS', () => {
  it('holds the activation path in fixed order (onboarding.md §1.2.1)', () => {
    expect(STEP_KEYS).toEqual([
      'create_workspace',
      'invite_member_or_add_agent',
      'create_first_issue',
      'dispatch_or_mention_agent',
      'see_agent_reply_in_inbox',
    ]);
  });
});

describe('isOnboardingStepKey', () => {
  it('accepts every activation step key', () => {
    for (const key of STEP_KEYS) {
      expect(isOnboardingStepKey(key)).toBe(true);
    }
  });

  it('rejects unknown keys', () => {
    expect(isOnboardingStepKey('bogus_step')).toBe(false);
    expect(isOnboardingStepKey('')).toBe(false);
  });
});
