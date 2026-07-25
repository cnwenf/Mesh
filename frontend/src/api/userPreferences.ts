/**
 * 账号偏好 API — auth.md §3.1 `PATCH /api/v1/users/me`。
 *
 * 写入 `settings.locale` / `settings.theme` / `timezone` 到服务端;
 * 422 unsupported_locale / 422 invalid_timezone 由调用方按 error.code 处理(§6.14 具名 code)。
 * 本地持久化(localStorage)作为降级镜像,服务端为权威真源。
 */
import type { MeshApiClient } from './client';
import type { ThemeMode } from '../state/settingsStore';

/** PATCH /api/v1/users/me 请求体(仅偏好相关字段) */
export interface UpdatePreferencesPayload {
  timezone?: string;
  settings?: {
    locale?: string | null;
    theme?: ThemeMode;
  };
}

/** GET /api/v1/me 响应中的用户偏好字段 */
export interface ServerUserPreferences {
  timezone: string | null;
  settings: {
    locale?: string | null;
    theme?: ThemeMode;
  };
}

/** 422 错误码:不支持的 locale / 非法时区(auth.md §3.1 canonical) */
export const ERROR_UNSUPPORTED_LOCALE = 'unsupported_locale';
export const ERROR_INVALID_TIMEZONE = 'invalid_timezone';

const USERS_ME_PATH = '/api/v1/users/me';
const ME_PATH = '/api/v1/me';

/**
 * 写入偏好到服务端(键级浅合并,auth.md §3.1)。
 * 成功返回更新后的完整用户对象;422 时抛 MeshApiError(code 为具名错误码)。
 */
export async function updatePreferences(
  client: MeshApiClient,
  payload: UpdatePreferencesPayload,
): Promise<ServerUserPreferences> {
  return client.request<ServerUserPreferences>('PATCH', USERS_ME_PATH, { body: payload });
}

/**
 * 读取当前用户偏好(GET /api/v1/me,auth.md §3.1)。
 * 用于应用启动时从服务端同步权威偏好。
 */
export async function fetchCurrentUserPreferences(
  client: MeshApiClient,
): Promise<ServerUserPreferences> {
  return client.request<ServerUserPreferences>('GET', ME_PATH);
}
