/**
 * quietHours 纯函数测试(comment-inbox.md §2.7):偏好提取 + 窗口判定(含跨午夜)。
 */
import { describe, expect, it } from 'vitest';
import { extractQuietHours, isInQuietHours } from '../quietHours';
import type { Preference } from '../types';

function pref(
  overrides: Partial<Preference> = {},
): Preference {
  return {
    event_type: 'all',
    in_app: true,
    email: 'none',
    quiet_hours_start: null,
    quiet_hours_end: null,
    ...overrides,
  };
}

describe('extractQuietHours', () => {
  it('returns null for an empty preference list', () => {
    expect(extractQuietHours([])).toBeNull();
  });

  it('returns null when no row has both start and end set', () => {
    expect(
      extractQuietHours([
        pref({ quiet_hours_start: '22:00:00' }),
        pref({ quiet_hours_end: '07:00:00' }),
        pref(),
      ]),
    ).toBeNull();
  });

  it('returns the first row with both start and end (first wins)', () => {
    expect(
      extractQuietHours([
        pref({ quiet_hours_start: '22:00:00' }),
        pref({ quiet_hours_start: '23:00:00', quiet_hours_end: '06:00:00' }),
        pref({ quiet_hours_start: '01:00:00', quiet_hours_end: '02:00:00' }),
      ]),
    ).toEqual({ start: '23:00:00', end: '06:00:00' });
  });

  it('skips rows with non-string values (boundary tolerance)', () => {
    const malformed = pref({
      quiet_hours_start: undefined as unknown as null,
      quiet_hours_end: undefined as unknown as null,
    });
    expect(extractQuietHours([malformed, pref({ quiet_hours_start: '08:00', quiet_hours_end: '09:00' })])).toEqual({
      start: '08:00',
      end: '09:00',
    });
  });
});

describe('isInQuietHours', () => {
  it('is true inside a same-day window', () => {
    expect(isInQuietHours('09:00', '17:00', { hour: 10, minute: 30 })).toBe(true);
  });

  it('is false outside a same-day window', () => {
    expect(isInQuietHours('09:00', '17:00', { hour: 8, minute: 59 })).toBe(false);
    expect(isInQuietHours('09:00', '17:00', { hour: 17, minute: 0 })).toBe(false);
  });

  it('includes the start boundary and excludes the end boundary', () => {
    expect(isInQuietHours('09:00', '17:00', { hour: 9, minute: 0 })).toBe(true);
    expect(isInQuietHours('09:00', '17:00', { hour: 16, minute: 59 })).toBe(true);
  });

  it('supports wrap-around-midnight windows', () => {
    expect(isInQuietHours('22:00', '07:00', { hour: 23, minute: 30 })).toBe(true);
    expect(isInQuietHours('22:00', '07:00', { hour: 0, minute: 0 })).toBe(true);
    expect(isInQuietHours('22:00', '07:00', { hour: 6, minute: 59 })).toBe(true);
    expect(isInQuietHours('22:00', '07:00', { hour: 7, minute: 0 })).toBe(false);
    expect(isInQuietHours('22:00', '07:00', { hour: 12, minute: 0 })).toBe(false);
    expect(isInQuietHours('22:00', '07:00', { hour: 21, minute: 59 })).toBe(false);
  });

  it('accepts HH:MM:SS values', () => {
    expect(isInQuietHours('22:00:00', '07:00:00', { hour: 23, minute: 0 })).toBe(true);
    expect(isInQuietHours('09:00:30', '17:00:30', { hour: 8, minute: 0 })).toBe(false);
  });

  it('returns false when start equals end (disabled window)', () => {
    expect(isInQuietHours('09:00', '09:00', { hour: 9, minute: 0 })).toBe(false);
    expect(isInQuietHours('09:00', '09:00', { hour: 22, minute: 0 })).toBe(false);
  });

  it('returns false for invalid time strings instead of throwing', () => {
    expect(isInQuietHours('garbage', '17:00', { hour: 10, minute: 0 })).toBe(false);
    expect(isInQuietHours('09:00', '17:xx', { hour: 10, minute: 0 })).toBe(false);
    expect(isInQuietHours('25:00', '17:00', { hour: 10, minute: 0 })).toBe(false);
    expect(isInQuietHours('09:61', '17:00', { hour: 10, minute: 0 })).toBe(false);
    expect(isInQuietHours('', '', { hour: 10, minute: 0 })).toBe(false);
    expect(isInQuietHours('-1:00', '17:00', { hour: 10, minute: 0 })).toBe(false);
  });
});
