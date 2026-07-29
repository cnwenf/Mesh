/**
 * 模块内变更广播测试(onboarding.md §4.2 流程 3:恢复后清单即时重现)。
 */
import { describe, expect, it, vi } from 'vitest';
import {
  notifyOnboardingExternalChange,
  onOnboardingExternalChange,
  onStepOptimisticRequest,
  requestOptimisticStepComplete,
} from '../notify';

describe('onOnboardingExternalChange / notifyOnboardingExternalChange', () => {
  it('notifies every subscriber exactly once per broadcast', () => {
    const a = vi.fn();
    const b = vi.fn();
    const offA = onOnboardingExternalChange(a);
    onOnboardingExternalChange(b);

    notifyOnboardingExternalChange();
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);

    notifyOnboardingExternalChange();
    expect(a).toHaveBeenCalledTimes(2);
    expect(b).toHaveBeenCalledTimes(2);

    offA();
    notifyOnboardingExternalChange();
    expect(a).toHaveBeenCalledTimes(2); // 已退订,不再收到
    expect(b).toHaveBeenCalledTimes(3);
  });

  it('survives a subscriber unsubscribing during broadcast', () => {
    const later = vi.fn();
    const selfRemoving = vi.fn(() => off());
    const off = onOnboardingExternalChange(selfRemoving);
    onOnboardingExternalChange(later);

    expect(() => notifyOnboardingExternalChange()).not.toThrow();
    expect(selfRemoving).toHaveBeenCalledTimes(1);
    expect(later).toHaveBeenCalledTimes(1);
  });
});

describe('onStepOptimisticRequest / requestOptimisticStepComplete(§1.2.2 O9)', () => {
  it('broadcasts the step key to every subscriber', () => {
    const a = vi.fn();
    const b = vi.fn();
    const offA = onStepOptimisticRequest(a);
    onStepOptimisticRequest(b);

    requestOptimisticStepComplete('create_first_issue');
    expect(a).toHaveBeenCalledWith({ stepKey: 'create_first_issue' });
    expect(b).toHaveBeenCalledWith({ stepKey: 'create_first_issue' });

    offA();
    requestOptimisticStepComplete('invite_member_or_add_agent');
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledWith({ stepKey: 'invite_member_or_add_agent' });
  });
});
