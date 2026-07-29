/**
 * 上手引导实时帧识别纯函数测试(onboarding.md §3.7)。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { isOnboardingFrame } from '../realtime';

const CHANNEL = 'member:mem-1:onboarding';

function frame(event: string, payload: Record<string, unknown>, channel = CHANNEL): RealtimeEventFrame {
  return { op: 'event', channel, seq: 1, event, payload };
}

describe('isOnboardingFrame', () => {
  it('accepts progress / completed frames on the member channel', () => {
    expect(isOnboardingFrame(frame('onboarding.progress', {}), CHANNEL)).toBe(true);
    expect(isOnboardingFrame(frame('onboarding.completed', {}), CHANNEL)).toBe(true);
  });

  it('rejects other channels, other events and non-event ops', () => {
    expect(isOnboardingFrame(frame('onboarding.progress', {}), 'member:mem-2:onboarding')).toBe(false);
    expect(isOnboardingFrame(frame('notification.read', {}), CHANNEL)).toBe(false);
    expect(
      isOnboardingFrame(
        { op: 'ping' } as unknown as RealtimeEventFrame,
        CHANNEL,
      ),
    ).toBe(false);
  });
});
