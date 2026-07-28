/**
 * 上手引导实时帧识别纯函数测试(onboarding.md §3.7)。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { isOnboardingFrame, parseOnboardingFrame } from '../realtime';

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

describe('parseOnboardingFrame', () => {
  it('parses a progress frame with a known step key', () => {
    const parsed = parseOnboardingFrame(
      frame('onboarding.progress', {
        state_id: 'obs-1',
        checklist: 'activation',
        step_key: 'create_first_issue',
        status: 'completed',
        completed_via: 'auto',
        progress: { total: 5, completed: 3, skipped: 0 },
      }),
    );
    expect(parsed).toEqual({ stateId: 'obs-1', kind: 'progress', stepKey: 'create_first_issue' });
  });

  it('parses a completed frame without a step key', () => {
    const parsed = parseOnboardingFrame(
      frame('onboarding.completed', {
        state_id: 'obs-1',
        checklist: 'activation',
        aha_reached_at: '2026-07-25T09:00:00Z',
        progress: { total: 5, completed: 5, skipped: 0 },
      }),
    );
    expect(parsed).toEqual({ stateId: 'obs-1', kind: 'completed', stepKey: null });
  });

  it('returns null stateId / stepKey when payload fields are absent or invalid', () => {
    expect(parseOnboardingFrame(frame('onboarding.progress', {}))).toEqual({
      stateId: null,
      kind: 'progress',
      stepKey: null,
    });
    expect(
      parseOnboardingFrame(frame('onboarding.progress', { state_id: 'obs-2', step_key: 'bogus' })),
    ).toEqual({ stateId: 'obs-2', kind: 'progress', stepKey: null });
  });

  it('returns null for non-onboarding frames', () => {
    expect(parseOnboardingFrame(frame('notification.created', {}))).toBeNull();
    expect(parseOnboardingFrame({ op: 'ping' } as unknown as RealtimeEventFrame)).toBeNull();
  });
});
