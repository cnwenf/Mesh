/**
 * 上手引导实体类型(onboarding.md §2.2/§2.3/§3.2)。
 * 清单归属人类成员(member×workspace×checklist);步骤固定五步,顺序即激活路径顺序(§1.2.1)。
 */

/** 激活路径五步(§1.2.1,顺序固定) */
export const STEP_KEYS = [
  'create_workspace',
  'invite_member_or_add_agent',
  'create_first_issue',
  'dispatch_or_mention_agent',
  'see_agent_reply_in_inbox',
] as const;

export type OnboardingStepKey = (typeof STEP_KEYS)[number];

export type OnboardingStepStatus = 'pending' | 'completed' | 'skipped';

export type OnboardingCompletionVia = 'auto' | 'manual';

export interface OnboardingStep {
  readonly step_key: OnboardingStepKey;
  readonly status: OnboardingStepStatus;
  readonly completed_via: OnboardingCompletionVia | null;
  readonly completed_at: string | null;
}

/** 服务端按步骤子表聚合的只读进度快照(§3.2) */
export interface OnboardingProgress {
  readonly total: number;
  readonly completed: number;
  readonly skipped: number;
}

export interface OnboardingState {
  readonly id: string;
  readonly workspace_id: string;
  readonly member_id: string;
  readonly checklist: string;
  /** aha moment 达成时间(末步完成时置位,仅一次) */
  readonly aha_reached_at: string | null;
  /** 整体关闭时间(NULL=未关闭;dismiss 幂等) */
  readonly dismissed_at: string | null;
  readonly progress: OnboardingProgress;
  readonly steps: readonly OnboardingStep[];
  readonly created_at: string;
  readonly updated_at: string;
}

export function isOnboardingStepKey(value: string): value is OnboardingStepKey {
  return (STEP_KEYS as readonly string[]).includes(value);
}
