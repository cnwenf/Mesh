/**
 * format.ts 展示层格式化测试(analytics.md §2.4/§6.18)。
 */
import { describe, expect, it } from 'vitest';
import {
  formatDurationSeconds,
  formatRate,
  rateTone,
  windowEndIso,
  windowStartIso,
} from '../format';

describe('formatDurationSeconds', () => {
  it('seconds only under a minute', () => {
    expect(formatDurationSeconds(45)).toBe('45s');
  });

  it('minutes and seconds', () => {
    expect(formatDurationSeconds(845)).toBe('14m 5s');
  });

  it('hours and minutes', () => {
    expect(formatDurationSeconds(3661)).toBe('1h 1m');
  });

  it('days and hours', () => {
    expect(formatDurationSeconds(90061)).toBe('1d 1h');
  });

  it('null or non-finite renders em dash', () => {
    expect(formatDurationSeconds(null)).toBe('—');
    expect(formatDurationSeconds(Number.NaN)).toBe('—');
  });
});

describe('formatRate', () => {
  it('percent with one decimal', () => {
    expect(formatRate(0.9091)).toBe('90.9%');
  });

  it('null renders em dash', () => {
    expect(formatRate(null)).toBe('—');
    expect(formatRate(Number.NaN)).toBe('—');
  });
});

describe('rateTone', () => {
  it('success at or above 0.9, warn at or above 0.7, danger below', () => {
    expect(rateTone(0.95)).toBe('success');
    expect(rateTone(0.9)).toBe('success');
    expect(rateTone(0.75)).toBe('warn');
    expect(rateTone(0.5)).toBe('danger');
    expect(rateTone(null)).toBe('warn');
  });
});

describe('window helpers', () => {
  it('start is daysBack before now in RFC3339 UTC (no millis)', () => {
    const now = new Date('2026-07-29T12:00:00.123Z');
    expect(windowStartIso(30, now)).toBe('2026-06-29T12:00:00Z');
    expect(windowEndIso(now)).toBe('2026-07-29T12:00:00Z');
  });

  it('defaults to the current instant', () => {
    expect(windowEndIso()).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
    expect(windowStartIso(30)).toMatch(/Z$/);
  });
});
