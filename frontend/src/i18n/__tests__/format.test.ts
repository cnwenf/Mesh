/**
 * 本地化渲染工具测试 — i18n.md §4.3/§4.4、README §6.18(展示层时区化 + 输入解析回 UTC)。
 * 断言值取自 Node 20 原生 Intl 实际输出,跨时区示例与 Spec §4.3 表格逐行对齐。
 */
import { describe, expect, it } from 'vitest';
import {
  formatDate,
  formatDateTime,
  formatNumber,
  formatRelativeTime,
  formatWithZoneAnnotation,
  isValidLocaleTag,
  isValidTimezone,
  parseLocalToUTC,
} from '../format';

const SPEC_UTC = '2026-07-25T18:00:00Z';

describe('formatDateTime(§4.4:日期/时间按 locale + 用户 timezone 渲染)', () => {
  it('zh-CN + Asia/Shanghai(默认 short/short)', () => {
    expect(formatDateTime(SPEC_UTC, { locale: 'zh-CN', timeZone: 'Asia/Shanghai' })).toBe(
      '2026/7/26 02:00',
    );
  });

  it('en-US + America/New_York(medium/short,夏令时 UTC-4)', () => {
    expect(
      formatDateTime(SPEC_UTC, {
        locale: 'en-US',
        timeZone: 'America/New_York',
        dateStyle: 'medium',
        timeStyle: 'short',
      }),
    ).toBe('Jul 25, 2026, 2:00 PM');
  });

  it('UTC 时区', () => {
    expect(formatDateTime(SPEC_UTC, { locale: 'en-US', timeZone: 'UTC' })).toBe('7/25/26, 6:00 PM');
  });

  it('非法 UTC 输入 → 抛错,绝不静默产出垃圾值', () => {
    expect(() => formatDateTime('', { locale: 'en' })).toThrow(/Invalid UTC timestamp/);
    expect(() => formatDateTime('not-a-date', { locale: 'en' })).toThrow(/Invalid UTC timestamp/);
    expect(() => formatDateTime('2026-13-45T00:00:00Z', { locale: 'en' })).toThrow(
      /Invalid UTC timestamp/,
    );
  });

  it('非法 locale / timezone → 抛清晰错误', () => {
    expect(() => formatDateTime(SPEC_UTC, { locale: '!!' })).toThrow(/Invalid locale/);
    expect(() => formatDateTime(SPEC_UTC, { locale: 'en', timeZone: 'Not/AZone' })).toThrow(
      /Invalid timezone/,
    );
  });
});

describe('formatDate(§4.4)', () => {
  it('日期(短):zh-CN → 2026/7/26;en-US → 7/26/26 (CLDR short 为两位年,以引擎实际输出为准)', () => {
    expect(formatDate(SPEC_UTC, { locale: 'zh-CN', timeZone: 'Asia/Shanghai' })).toBe('2026/7/26');
    expect(formatDate(SPEC_UTC, { locale: 'en-US', timeZone: 'Asia/Shanghai' })).toBe('7/26/26');
  });

  it('日期(中/长)', () => {
    expect(formatDate(SPEC_UTC, { locale: 'en-US', timeZone: 'UTC', dateStyle: 'medium' })).toBe(
      'Jul 25, 2026',
    );
    expect(
      formatDate(SPEC_UTC, { locale: 'zh-CN', timeZone: 'Asia/Shanghai', dateStyle: 'long' }),
    ).toBe('2026年7月26日');
  });
});

describe('formatNumber(§4.4:千分位/小数点/货币按 locale)', () => {
  it('千分位:zh-CN 1,234.5;de-DE 1.234,5', () => {
    expect(formatNumber(1234.5, { locale: 'zh-CN' })).toBe('1,234.5');
    expect(formatNumber(1234.5, { locale: 'de-DE' })).toBe('1.234,5');
  });

  it('百分数与货币', () => {
    expect(formatNumber(0.42, { locale: 'en-US', style: 'percent' })).toBe('42%');
    expect(formatNumber(9.5, { locale: 'en-US', style: 'currency', currency: 'USD' })).toBe(
      '$9.50',
    );
  });

  it('currency 样式缺少 currency → 抛错;非法数值 → 抛错', () => {
    expect(() => formatNumber(1, { locale: 'en', style: 'currency' })).toThrow(/currency/);
    expect(() => formatNumber(Number.NaN, { locale: 'en' })).toThrow(/Invalid number/);
    expect(() => formatNumber(Number.POSITIVE_INFINITY, { locale: 'en' })).toThrow(
      /Invalid number/,
    );
  });

  it('非法 locale → 抛清晰错误', () => {
    expect(() => formatNumber(1, { locale: '!!' })).toThrow(/Invalid locale/);
  });
});

describe('formatRelativeTime(§4.4:相对时间按 locale + 复数规则)', () => {
  const now = new Date('2026-07-25T12:00:00Z');
  const at = (minutes: number): string => new Date(now.getTime() + minutes * 60_000).toISOString();

  it('zh-CN:3 分钟前 / 2 小时后', () => {
    expect(formatRelativeTime(at(-3), { locale: 'zh-CN', now })).toBe('3分钟前');
    expect(formatRelativeTime(at(120), { locale: 'zh-CN', now })).toBe('2小时后');
  });

  it('en:3 minutes ago / in 2 hours(en 区分 one/other 复数)', () => {
    expect(formatRelativeTime(at(-3), { locale: 'en', now })).toBe('3 minutes ago');
    expect(formatRelativeTime(at(-1), { locale: 'en', now })).toBe('1 minute ago');
    expect(formatRelativeTime(at(120), { locale: 'en', now })).toBe('in 2 hours');
  });

  it('自动单位:秒 → 分 → 时 → 天', () => {
    expect(formatRelativeTime(at(-0.75), { locale: 'en', now })).toBe('45 seconds ago');
    expect(formatRelativeTime(at(-30), { locale: 'en', now })).toBe('30 minutes ago');
    expect(formatRelativeTime(at(-300), { locale: 'en', now })).toBe('5 hours ago');
    expect(formatRelativeTime(at(-60 * 48), { locale: 'en', now })).toBe('2 days ago');
  });

  it('非法输入 → 抛错', () => {
    expect(() => formatRelativeTime('nope', { locale: 'en' })).toThrow(/Invalid UTC timestamp/);
    expect(() => formatRelativeTime(SPEC_UTC, { locale: '!!' })).toThrow(/Invalid locale/);
  });
});

describe('formatWithZoneAnnotation(§4.3 跨时区示例表)', () => {
  it('Asia/Shanghai(UTC+8)→ 2026-07-26 02:00 (GMT+8)', () => {
    expect(formatWithZoneAnnotation(SPEC_UTC, { locale: 'zh-CN', timeZone: 'Asia/Shanghai' })).toBe(
      '2026-07-26 02:00 (GMT+8)',
    );
  });

  it('America/New_York(UTC-4,夏令时)→ 2026-07-25 14:00 (GMT-4)', () => {
    expect(
      formatWithZoneAnnotation(SPEC_UTC, { locale: 'en-US', timeZone: 'America/New_York' }),
    ).toBe('2026-07-25 14:00 (GMT-4)');
  });

  it('UTC → 2026-07-25 18:00 (UTC)', () => {
    expect(formatWithZoneAnnotation(SPEC_UTC, { locale: 'en-US', timeZone: 'UTC' })).toBe(
      '2026-07-25 18:00 (UTC)',
    );
  });

  it('未传 timezone 默认 UTC(确定性,不随运行环境漂移)', () => {
    expect(formatWithZoneAnnotation(SPEC_UTC, { locale: 'en-US' })).toBe('2026-07-25 18:00 (UTC)');
  });

  it('非法 timezone / 非法时间 → 抛错', () => {
    expect(() =>
      formatWithZoneAnnotation(SPEC_UTC, { locale: 'en', timeZone: 'Mars/Olympus' }),
    ).toThrow(/Invalid timezone/);
    expect(() => formatWithZoneAnnotation('garbage', { locale: 'en' })).toThrow(
      /Invalid UTC timestamp/,
    );
  });
});

describe('parseLocalToUTC(§6.18:本地时间输入解析回 UTC 存储)', () => {
  it('formatWithZoneAnnotation 的逆运算:Shanghai 2026-07-26 02:00 → 2026-07-25T18:00:00.000Z', () => {
    expect(
      parseLocalToUTC({ year: 2026, month: 7, day: 26, hour: 2, minute: 0 }, 'Asia/Shanghai'),
    ).toBe('2026-07-25T18:00:00.000Z');
  });

  it('America/New_York(夏令时)2026-07-25 14:00 → 同一 UTC 瞬时', () => {
    expect(
      parseLocalToUTC({ year: 2026, month: 7, day: 25, hour: 14, minute: 0 }, 'America/New_York'),
    ).toBe('2026-07-25T18:00:00.000Z');
  });

  it('UTC 恒等', () => {
    expect(parseLocalToUTC({ year: 2026, month: 7, day: 25, hour: 18, minute: 0 }, 'UTC')).toBe(
      '2026-07-25T18:00:00.000Z',
    );
  });

  it('hour/minute 缺省为 0', () => {
    expect(parseLocalToUTC({ year: 2026, month: 7, day: 26 }, 'Asia/Shanghai')).toBe(
      '2026-07-25T16:00:00.000Z',
    );
  });

  it('DST 回拨的歧义本地时间(取第一次出现/较早偏移,确定性,同 Temporal compatible)', () => {
    expect(
      parseLocalToUTC({ year: 2026, month: 11, day: 1, hour: 1, minute: 30 }, 'America/New_York'),
    ).toBe('2026-11-01T05:30:00.000Z');
  });

  it('DST 跳跃缺口的本地时间(取缺口后等价时刻,不抛错)', () => {
    // America/New_York 2026-03-08 02:30 不存在(02:00→03:00);确定性解析,不抛错
    expect(() =>
      parseLocalToUTC({ year: 2026, month: 3, day: 8, hour: 2, minute: 30 }, 'America/New_York'),
    ).not.toThrow();
  });

  it('非法字段 / 非法时区 → 抛清晰错误', () => {
    expect(() => parseLocalToUTC({ year: 2026, month: 13, day: 1 }, 'UTC')).toThrow(/month/);
    expect(() => parseLocalToUTC({ year: 2026, month: 7, day: 0 }, 'UTC')).toThrow(/day/);
    expect(() => parseLocalToUTC({ year: 2026, month: 7, day: 1, hour: 24 }, 'UTC')).toThrow(
      /hour/,
    );
    expect(() => parseLocalToUTC({ year: 2026, month: 7, day: 1, minute: 60 }, 'UTC')).toThrow(
      /minute/,
    );
    expect(() => parseLocalToUTC({ year: 2026.5, month: 7, day: 1 }, 'UTC')).toThrow(/year/);
    expect(() => parseLocalToUTC({ year: 2026, month: 7, day: 1 }, 'Not/AZone')).toThrow(
      /timezone/,
    );
  });
});

describe('isValidTimezone / isValidLocaleTag(BCP-47 与 IANA 校验)', () => {
  it('合法/非法 IANA 时区', () => {
    expect(isValidTimezone('Asia/Shanghai')).toBe(true);
    expect(isValidTimezone('UTC')).toBe(true);
    expect(isValidTimezone('Not/AZone')).toBe(false);
    expect(isValidTimezone('')).toBe(false);
  });

  it('BCP-47 语法校验(Intl.Locale)', () => {
    expect(isValidLocaleTag('zh-CN')).toBe(true);
    expect(isValidLocaleTag('en')).toBe(true);
    expect(isValidLocaleTag('en-US')).toBe(true);
    expect(isValidLocaleTag('!!')).toBe(false);
    expect(isValidLocaleTag('')).toBe(false);
  });
});
