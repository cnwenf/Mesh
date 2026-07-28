/**
 * Autopilot 展示层格式化(autopilot.md §4.2 / README §6.18:时区化仅展示层)。
 * 纯函数,无副作用,便于单测。
 */
import type { AutopilotRunStatus, AutopilotStatus } from './types';
import type { StatusDotTone } from '../../design';

export const RULE_STATUS_TONE: Record<AutopilotStatus, StatusDotTone> = {
  active: 'success',
  paused: 'warn',
  archived: 'neutral',
};

export const RUN_STATUS_TONE: Record<AutopilotRunStatus, StatusDotTone> = {
  pending: 'info',
  running: 'info',
  waiting_approval: 'warn',
  retrying: 'warn',
  succeeded: 'success',
  failed: 'danger',
  cancelled: 'neutral',
};

/** 运行时长(ms → 人类可读,§4.2 时间线列)。 */
export function formatDurationMs(durationMs: number | null): string | null {
  if (durationMs === null || durationMs < 0) return null;
  if (durationMs < 1000) return `${durationMs}ms`;
  const seconds = Math.round(durationMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const restSeconds = seconds % 60;
  if (minutes < 60) return restSeconds > 0 ? `${minutes}m ${restSeconds}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes > 0 ? `${hours}h ${restMinutes}m` : `${hours}h`;
}

/** 成功率渲染(0–1 → 百分比;无数据 → null)。 */
export function formatSuccessRate(rate: number | null): string | null {
  if (rate === null) return null;
  return `${Math.round(rate * 100)}%`;
}

/** 触发器人类可读摘要(列表「触发器」列,§4.1)。 */
export function scheduleSummary(
  triggerConfig: Readonly<Record<string, unknown>>,
): string | null {
  const cron = triggerConfig.cron;
  const timezone = triggerConfig.timezone;
  if (typeof cron !== 'string' || typeof timezone !== 'string') return null;
  return `${cron} (${timezone})`;
}

/** 相对时间(列表「上次运行」列;展示层本地化由 Intl 完成)。 */
export function formatRelativeTime(iso: string | null, nowMs: number, locale: string): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const diffSeconds = Math.round((nowMs - then) / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  if (Math.abs(diffSeconds) < 60) return rtf.format(-diffSeconds, 'second');
  const diffMinutes = Math.round(diffSeconds / 60);
  if (Math.abs(diffMinutes) < 60) return rtf.format(-diffMinutes, 'minute');
  const diffHours = Math.round(diffMinutes / 60);
  if (Math.abs(diffHours) < 24) return rtf.format(-diffHours, 'hour');
  const diffDays = Math.round(diffHours / 24);
  return rtf.format(-diffDays, 'day');
}

/** 错误摘要(运行时间线 / 详情页错误行)。 */
export function errorSummary(error: Readonly<Record<string, unknown>> | null): string | null {
  if (!error) return null;
  const code = typeof error.code === 'string' ? error.code : 'error';
  const message = typeof error.message === 'string' ? error.message : '';
  return message ? `${code}: ${message}` : code;
}
