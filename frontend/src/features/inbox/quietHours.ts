/**
 * 免打扰时段(quiet hours)纯函数模块(comment-inbox.md §2.7 / §4.2):
 * 从通知偏好提取免打扰窗口(首条 start/end 均非空的行),并判断给定时刻是否落在
 * 窗口内(支持跨午夜窗口,如 22:00→07:00)。纯函数、不读当前时间(now 由调用方
 * 注入),便于确定性测试;非法时间串视为未配置(返回 false,不抛错)。
 */
import type { Preference } from './types';

export interface QuietHours {
  readonly start: string;
  readonly end: string;
}

export interface ClockTime {
  readonly hour: number;
  readonly minute: number;
}

/** 提取免打扰窗口:取首条 start/end 均为字符串的行;无则 null。 */
export function extractQuietHours(prefs: readonly Preference[]): QuietHours | null {
  for (const pref of prefs) {
    if (typeof pref.quiet_hours_start === 'string' && typeof pref.quiet_hours_end === 'string') {
      return { start: pref.quiet_hours_start, end: pref.quiet_hours_end };
    }
  }
  return null;
}

/** 解析 'HH:MM' 或 'HH:MM:SS' 为当日分钟数;非法格式 / 越界返回 null。 */
function toMinutes(value: string): number | null {
  const parts = value.split(':');
  if (parts.length < 2) return null;
  const hour = Number(parts[0]);
  const minute = Number(parts[1]);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return null;
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return hour * 60 + minute;
}

/**
 * 判断 now 是否处于免打扰窗口 [start, end)。
 * - start > end 视为跨午夜([start, 24:00) ∪ [00:00, end));
 * - start === end 视为未启用(恒 false);
 * - 任一时间串非法 → false(容错,不抛错)。
 */
export function isInQuietHours(start: string, end: string, now: ClockTime): boolean {
  const startMinutes = toMinutes(start);
  const endMinutes = toMinutes(end);
  if (startMinutes === null || endMinutes === null) return false;
  if (startMinutes === endMinutes) return false;
  const nowMinutes = now.hour * 60 + now.minute;
  if (startMinutes < endMinutes) {
    return nowMinutes >= startMinutes && nowMinutes < endMinutes;
  }
  return nowMinutes >= startMinutes || nowMinutes < endMinutes;
}
