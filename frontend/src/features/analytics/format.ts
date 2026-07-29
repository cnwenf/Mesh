/**
 * 统计报表展示层格式化(analytics.md §2.4/§6.18):服务端只给 UTC 锚点,
 * 本地化渲染在此(时区取响应 meta.display_timezone 回显值)。
 */

/** 时长(秒)→ 紧凑人类可读(如 845 → "14m 5s",90061 → "1d 1h")。 */
export function formatDurationSeconds(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return '—';
  const total = Math.round(seconds);
  if (total < 60) return `${total}s`;
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m ${secs}s`;
}

/** 比率 → 百分比文本(null → "—")。 */
export function formatRate(rate: number | null): string {
  if (rate === null || !Number.isFinite(rate)) return '—';
  return `${(rate * 100).toFixed(1)}%`;
}

/** 窗口起点(UTC RFC3339,now 基准)的 ISO 串。 */
export function windowStartIso(daysBack: number, now: Date = new Date()): string {
  const start = new Date(now.getTime() - daysBack * 86400 * 1000);
  return start.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

export function windowEndIso(now: Date = new Date()): string {
  return now.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** 成功率语义色档位(§4.5:颜色不作唯一信号,调用方同时给文本)。 */
export function rateTone(rate: number | null): 'success' | 'warn' | 'danger' {
  if (rate === null) return 'warn';
  if (rate >= 0.9) return 'success';
  if (rate >= 0.7) return 'warn';
  return 'danger';
}
