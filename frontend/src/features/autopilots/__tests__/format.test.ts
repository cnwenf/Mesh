/**
 * format.ts 纯函数单测(autopilot.md §4.2 展示层)。
 */
import { describe, expect, it } from 'vitest';
import {
  RUN_STATUS_TONE,
  errorSummary,
  formatDurationMs,
  formatRelativeTime,
  formatSuccessRate,
  scheduleSummary,
} from '../format';

describe('formatDurationMs', () => {
  it('renders ms below a second', () => {
    expect(formatDurationMs(350)).toBe('350ms');
  });

  it('renders seconds and minutes', () => {
    expect(formatDurationMs(5_000)).toBe('5s');
    expect(formatDurationMs(90_000)).toBe('1m 30s');
    expect(formatDurationMs(120_000)).toBe('2m');
  });

  it('renders hours', () => {
    expect(formatDurationMs(3_600_000)).toBe('1h');
    expect(formatDurationMs(5_400_000)).toBe('1h 30m');
  });

  it('returns null for null or negative', () => {
    expect(formatDurationMs(null)).toBeNull();
    expect(formatDurationMs(-1)).toBeNull();
  });
});

describe('formatSuccessRate', () => {
  it('formats ratios as percents and passes null through', () => {
    expect(formatSuccessRate(0.95)).toBe('95%');
    expect(formatSuccessRate(null)).toBeNull();
  });
});

describe('scheduleSummary', () => {
  it('renders cron with timezone', () => {
    expect(scheduleSummary({ cron: '0 9 * * *', timezone: 'UTC' })).toBe('0 9 * * * (UTC)');
  });

  it('returns null when fields missing', () => {
    expect(scheduleSummary({})).toBeNull();
    expect(scheduleSummary({ cron: 5 })).toBeNull();
  });
});

describe('formatRelativeTime', () => {
  const now = Date.parse('2026-07-27T12:00:00Z');

  it('formats past times relatively', () => {
    expect(formatRelativeTime('2026-07-27T11:59:30Z', now, 'en')).toContain('second');
    expect(formatRelativeTime('2026-07-27T11:00:00Z', now, 'en')).toContain('hour');
    expect(formatRelativeTime('2026-07-25T12:00:00Z', now, 'en')).toContain('day');
  });

  it('handles null and invalid input', () => {
    expect(formatRelativeTime(null, now, 'en')).toBeNull();
    expect(formatRelativeTime('not-a-date', now, 'en')).toBeNull();
  });
});

describe('errorSummary', () => {
  it('combines code and message', () => {
    expect(errorSummary({ code: 'timeout', message: 'outbound timed out' })).toBe(
      'timeout: outbound timed out',
    );
  });

  it('falls back to code alone', () => {
    expect(errorSummary({ code: 'rate_limited' })).toBe('rate_limited');
    expect(errorSummary(null)).toBeNull();
    expect(errorSummary({})).toBe('error');
  });
});

describe('RUN_STATUS_TONE', () => {
  it('maps every run status', () => {
    expect(RUN_STATUS_TONE.waiting_approval).toBe('warn');
    expect(RUN_STATUS_TONE.succeeded).toBe('success');
    expect(RUN_STATUS_TONE.failed).toBe('danger');
  });
});
