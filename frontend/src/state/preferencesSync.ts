/**
 * 偏好服务端同步 — 将 settingsStore 的 locale/theme/timezone 写入
 * `PATCH /api/v1/users/me`(auth.md §3.1),本地持久化作为降级镜像。
 *
 * 设计:
 * - 写入为 fire-and-forget 异步:本地状态立即生效(乐观更新),
 *   服务端失败不回滚本地(降级镜像策略),仅上报错误供 UI 提示;
 * - 422 unsupported_locale / 422 invalid_timezone 为可预期的用户输入错误,
 *   经 onError 回调通知调用方(§6.14 具名 code → i18n 错误文案);
 * - 网络错误静默降级(离线场景本地偏好仍可用)。
 */
import type { MeshApiClient } from '../api/client';
import { MeshApiError } from '../api/errors';
import {
  ERROR_INVALID_THEME_MODE,
  ERROR_INVALID_TIMEZONE,
  ERROR_UNSUPPORTED_LOCALE,
  updatePreferences,
} from '../api/userPreferences';
import type { UpdatePreferencesPayload } from '../api/userPreferences';
import { enqueueFailedWrite, replayPendingWrites } from './pendingSettingsQueue';
import type { ThemeMode, UserPreferences } from './settingsStore';

/** 偏好同步错误类型(供 UI 层按 code 渲染 i18n 错误提示) */
export interface PreferenceSyncError {
  readonly code:
    | 'unsupported_locale'
    | 'invalid_timezone'
    | 'invalid_theme_mode'
    | 'network'
    | 'server';
  readonly message: string;
  readonly status: number;
}

export interface SyncPreferencesOptions {
  /** 同步失败时的错误回调(422 具名错误与网络/服务端错误) */
  onError?: (error: PreferenceSyncError) => void;
}

/** 将 UserPreferences 映射为 PATCH 请求体 */
export function toUpdatePayload(preferences: UserPreferences): UpdatePreferencesPayload {
  const payload: UpdatePreferencesPayload = {
    timezone: preferences.timezone,
    settings: {},
  };
  if (preferences.locale !== null) {
    payload.settings!.locale = preferences.locale;
  }
  payload.settings!.theme = preferences.theme;
  return payload;
}

/** 将 MeshApiError 归一为 PreferenceSyncError */
function toSyncError(err: unknown): PreferenceSyncError {
  if (err instanceof MeshApiError) {
    if (
      err.code === ERROR_UNSUPPORTED_LOCALE ||
      err.code === ERROR_INVALID_TIMEZONE ||
      err.code === ERROR_INVALID_THEME_MODE
    ) {
      return { code: err.code, message: err.message, status: err.status };
    }
    if (err.status === 0) {
      return { code: 'network', message: err.message, status: 0 };
    }
    return { code: 'server', message: err.message, status: err.status };
  }
  return {
    code: 'server',
    message: err instanceof Error ? err.message : 'unknown error',
    status: 0,
  };
}

/**
 * 将偏好同步到服务端(fire-and-forget)。
 * 本地状态已由 settingsStore 即时更新;本函数仅负责服务端写入与错误上报。
 */
export async function syncPreferencesToServer(
  client: MeshApiClient,
  preferences: UserPreferences,
  options: SyncPreferencesOptions = {},
): Promise<void> {
  const payload = toUpdatePayload(preferences);
  try {
    await updatePreferences(client, payload);
    // 写入成功 → 顺带按序重放 pending(§4.5 触发点之一);重放失败静默保留。
    void replayPendingWrites(client);
  } catch (err: unknown) {
    enqueueFailedWrite(payload); // 乐观不回滚,失败写入进分区队列
    const syncError = toSyncError(err);
    options.onError?.(syncError);
  }
}

/**
 * 单独同步 theme(仅 settings.theme 字段变更时调用,减少不必要的全量写入)。
 * `null` = 显式清除、恢复跟随工作区默认(theme.md §3.2:后端对显式 null
 * 执行 key pop;非法值 → 422 invalid_theme_mode 经 onError 归一)。
 */
export async function syncThemeToServer(
  client: MeshApiClient,
  theme: ThemeMode | null,
  options: SyncPreferencesOptions = {},
): Promise<void> {
  const payload: UpdatePreferencesPayload = { settings: { theme } };
  try {
    await updatePreferences(client, payload);
    void replayPendingWrites(client);
  } catch (err: unknown) {
    enqueueFailedWrite(payload);
    options.onError?.(toSyncError(err));
  }
}

/**
 * 单独同步 locale(仅 settings.locale 字段变更时调用)。
 */
export async function syncLocaleToServer(
  client: MeshApiClient,
  locale: string | null,
  options: SyncPreferencesOptions = {},
): Promise<void> {
  const payload: UpdatePreferencesPayload = { settings: {} };
  // locale=null 表示"恢复跟随默认",向服务端发送显式 null 以清除偏好
  // (后端 PATCH 对显式 null 执行 merged.pop('locale'),auth.md §3.1 清除语义)
  payload.settings!.locale = locale;
  try {
    await updatePreferences(client, payload);
    void replayPendingWrites(client);
  } catch (err: unknown) {
    enqueueFailedWrite(payload);
    options.onError?.(toSyncError(err));
  }
}

/**
 * 单独同步 timezone(仅 timezone 字段变更时调用)。
 */
export async function syncTimezoneToServer(
  client: MeshApiClient,
  timezone: string,
  options: SyncPreferencesOptions = {},
): Promise<void> {
  const payload: UpdatePreferencesPayload = { timezone };
  try {
    await updatePreferences(client, payload);
    void replayPendingWrites(client);
  } catch (err: unknown) {
    enqueueFailedWrite(payload);
    options.onError?.(toSyncError(err));
  }
}
