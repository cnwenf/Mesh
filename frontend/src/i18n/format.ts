/**
 * 本地化渲染工具 — i18n.md §4.3/§4.4、README §6.18。
 *
 * 原则:
 * - 存储与传输恒 UTC RFC3339;时区化仅发生在展示层(users.timezone 决定本地显示);
 * - 一律原生 Intl(无第三方日期库);
 * - 非法输入显式抛错,绝不静默产出垃圾值。
 */

export type DateStyle = 'short' | 'medium' | 'long';
export type TimeStyle = 'short' | 'medium' | 'long';

const MS_PER_SECOND = 1_000;
const MS_PER_MINUTE = 60 * MS_PER_SECOND;
const MS_PER_HOUR = 60 * MS_PER_MINUTE;
const MS_PER_DAY = 24 * MS_PER_HOUR;

const ZERO_OFFSET_ANNOTATIONS = new Set(['GMT', 'GMT+0', 'GMT-0', 'GMT+00', 'GMT-00', 'UTC']);
const UTC_ANNOTATION = 'UTC';
const DEFAULT_TIMEZONE = 'UTC';

/** RFC3339 UTC 输入 → Date;非法输入抛清晰错误(绝不静默)。 */
function toUtcDate(utcIso: string): Date {
  if (typeof utcIso !== 'string' || utcIso.trim().length === 0) {
    throw new Error(
      `Invalid UTC timestamp: expected an RFC 3339 string, received ${JSON.stringify(utcIso)}`,
    );
  }
  const date = new Date(utcIso);
  if (Number.isNaN(date.getTime())) {
    throw new Error(`Invalid UTC timestamp: "${utcIso}" cannot be parsed as RFC 3339`);
  }
  return date;
}

/** 构造 DateTimeFormat,将引擎错误归一为清晰的 locale/timezone 错误。 */
function buildDateTimeFormat(
  locale: string,
  options: Intl.DateTimeFormatOptions,
  timeZone: string | undefined,
): Intl.DateTimeFormat {
  try {
    return new Intl.DateTimeFormat(locale, options);
  } catch (error) {
    if (!isValidLocaleTag(locale)) throw new Error(`Invalid locale: "${locale}"`);
    if (timeZone !== undefined && !isValidTimezone(timeZone)) {
      throw new Error(`Invalid timezone: "${timeZone}"`);
    }
    throw error;
  }
}

export interface FormatDateTimeOptions {
  readonly locale: string;
  readonly timeZone?: string;
  readonly dateStyle?: DateStyle;
  readonly timeStyle?: TimeStyle;
}

/** 绝对时间按 locale + 用户 timezone 渲染(§4.4)。 */
export function formatDateTime(utcIso: string, opts: FormatDateTimeOptions): string {
  const date = toUtcDate(utcIso);
  const formatter = buildDateTimeFormat(
    opts.locale,
    {
      timeZone: opts.timeZone,
      dateStyle: opts.dateStyle ?? 'short',
      timeStyle: opts.timeStyle ?? 'short',
    },
    opts.timeZone,
  );
  return formatter.format(date);
}

export interface FormatDateOptions {
  readonly locale: string;
  readonly timeZone?: string;
  readonly dateStyle?: DateStyle;
}

/** 仅日期(截止日等)按 locale + 用户 timezone 渲染。 */
export function formatDate(utcIso: string, opts: FormatDateOptions): string {
  const date = toUtcDate(utcIso);
  const formatter = buildDateTimeFormat(
    opts.locale,
    { timeZone: opts.timeZone, dateStyle: opts.dateStyle ?? 'short' },
    opts.timeZone,
  );
  return formatter.format(date);
}

export type NumberStyle = 'decimal' | 'percent' | 'currency';

export interface FormatNumberOptions {
  readonly locale: string;
  readonly style?: NumberStyle;
  readonly currency?: string;
}

/** 数字本地化(千分位/小数点/货币,§4.4)。 */
export function formatNumber(value: number, opts: FormatNumberOptions): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`Invalid number: expected a finite number, received ${String(value)}`);
  }
  const style = opts.style ?? 'decimal';
  if (style === 'currency' && (typeof opts.currency !== 'string' || opts.currency.length === 0)) {
    throw new Error("formatNumber: currency is required when style is 'currency'");
  }
  try {
    const options: Intl.NumberFormatOptions = { style };
    if (style === 'currency') options.currency = opts.currency;
    return new Intl.NumberFormat(opts.locale, options).format(value);
  } catch (error) {
    if (!isValidLocaleTag(opts.locale)) throw new Error(`Invalid locale: "${opts.locale}"`);
    throw error;
  }
}

export interface FormatRelativeTimeOptions {
  readonly locale: string;
  readonly now?: Date;
}

/**
 * 相对时间("3 分钟前" / "in 2 hours",§4.4/§L5)。
 * 自动单位:秒 → 分 → 时 → 天;复数规则由 Intl.RelativeTimeFormat 按 CLDR 处理。
 */
export function formatRelativeTime(utcIso: string, opts: FormatRelativeTimeOptions): string {
  const date = toUtcDate(utcIso);
  const now = opts.now ?? new Date();
  const diffMs = date.getTime() - now.getTime();
  const absolute = Math.abs(diffMs);
  let unit: Intl.RelativeTimeFormatUnit;
  let unitMs: number;
  if (absolute < MS_PER_MINUTE) {
    unit = 'second';
    unitMs = MS_PER_SECOND;
  } else if (absolute < MS_PER_HOUR) {
    unit = 'minute';
    unitMs = MS_PER_MINUTE;
  } else if (absolute < MS_PER_DAY) {
    unit = 'hour';
    unitMs = MS_PER_HOUR;
  } else {
    unit = 'day';
    unitMs = MS_PER_DAY;
  }
  const quantity = Math.round(diffMs / unitMs);
  try {
    return new Intl.RelativeTimeFormat(opts.locale, { numeric: 'always' }).format(quantity, unit);
  } catch (error) {
    if (!isValidLocaleTag(opts.locale)) throw new Error(`Invalid locale: "${opts.locale}"`);
    throw error;
  }
}

export interface FormatWithZoneAnnotationOptions {
  readonly locale: string;
  readonly timeZone?: string;
}

/**
 * 跨时区标注(§4.3):`YYYY-MM-DD HH:mm (GMT+8)`。
 * 时间部分为固定 24 小时制数字格式(与 Spec §4.3 表格逐字一致),
 * 括号内为该时区在目标瞬时的偏移标注(UTC 时区标注 `UTC`)。
 * 未传 timeZone 默认 UTC(确定性,不随运行环境漂移)。
 */
export function formatWithZoneAnnotation(
  utcIso: string,
  opts: FormatWithZoneAnnotationOptions,
): string {
  const date = toUtcDate(utcIso);
  const timeZone = opts.timeZone ?? DEFAULT_TIMEZONE;
  if (!isValidTimezone(timeZone)) throw new Error(`Invalid timezone: "${timeZone}"`);
  if (!isValidLocaleTag(opts.locale)) throw new Error(`Invalid locale: "${opts.locale}"`);
  const parts = wallClockParts(date, timeZone);
  const wallClock = `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  return `${wallClock} (${zoneAnnotation(date, timeZone)})`;
}

interface WallClockParts {
  readonly year: string;
  readonly month: string;
  readonly day: string;
  readonly hour: string;
  readonly minute: string;
}

function wallClockParts(date: Date, timeZone: string): WallClockParts {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(date);
  const pick = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? '00';
  return {
    year: pick('year'),
    month: pick('month'),
    day: pick('day'),
    hour: pick('hour'),
    minute: pick('minute'),
  };
}

/** 时区偏移标注:shortOffset 归一化(UTC 时区 → `UTC`,其余 `GMT±h`)。 */
function zoneAnnotation(date: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    timeZoneName: 'shortOffset',
  }).formatToParts(date);
  const raw = parts.find((part) => part.type === 'timeZoneName')?.value ?? UTC_ANNOTATION;
  return ZERO_OFFSET_ANNOTATIONS.has(raw) ? UTC_ANNOTATION : raw;
}

export interface LocalTimeParts {
  readonly year: number;
  readonly month: number;
  readonly day: number;
  readonly hour?: number;
  readonly minute?: number;
}

/**
 * 本地挂钟时间输入 → RFC3339 UTC(§6.18:展示层输入解析回 UTC 存储)。
 * 两次偏移逼近处理 DST 边界;回拨歧义取第一次出现(较早偏移,与 Temporal
 * `compatible` 约定一致,确定性);跳跃缺口取缺口后的挂钟等价时刻。
 */
export function parseLocalToUTC(parts: LocalTimeParts, timeZone: string): string {
  assertIntegerInRange(parts.year, 1, 9999, 'year');
  assertIntegerInRange(parts.month, 1, 12, 'month');
  assertIntegerInRange(parts.day, 1, 31, 'day');
  const hour = parts.hour ?? 0;
  const minute = parts.minute ?? 0;
  assertIntegerInRange(hour, 0, 23, 'hour');
  assertIntegerInRange(minute, 0, 59, 'minute');
  if (!isValidTimezone(timeZone)) throw new Error(`Invalid timezone: "${timeZone}"`);
  const localAsUtcMs = Date.UTC(parts.year, parts.month - 1, parts.day, hour, minute);
  const firstOffset = zoneOffsetMs(new Date(localAsUtcMs), timeZone);
  const secondOffset = zoneOffsetMs(new Date(localAsUtcMs - firstOffset), timeZone);
  return new Date(localAsUtcMs - secondOffset).toISOString();
}

/** 指定时区在某瞬时的偏移量(本地 − UTC,毫秒)。 */
function zoneOffsetMs(date: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(date);
  const pick = (type: Intl.DateTimeFormatPartTypes): number =>
    Number(parts.find((part) => part.type === type)?.value ?? 0);
  const localAsUtcMs = Date.UTC(
    pick('year'),
    pick('month') - 1,
    pick('day'),
    pick('hour'),
    pick('minute'),
    pick('second'),
  );
  return localAsUtcMs - Math.floor(date.getTime() / MS_PER_SECOND) * MS_PER_SECOND;
}

function assertIntegerInRange(value: number, min: number, max: number, field: string): void {
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(
      `parseLocalToUTC: ${field} must be an integer between ${min} and ${max}, received ${String(value)}`,
    );
  }
}

/** 合法 IANA 时区校验(Intl.DateTimeFormat 探测)。 */
export function isValidTimezone(tz: string): boolean {
  if (typeof tz !== 'string' || tz.length === 0) return false;
  try {
    new Intl.DateTimeFormat('en', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** BCP-47 语法校验(Intl.Locale)。 */
export function isValidLocaleTag(tag: string): boolean {
  if (typeof tag !== 'string' || tag.length === 0) return false;
  try {
    new Intl.Locale(tag);
    return true;
  } catch {
    return false;
  }
}
