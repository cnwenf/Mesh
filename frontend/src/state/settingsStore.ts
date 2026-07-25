/**
 * 账号级展示偏好(theme/locale/timezone)— README §6.12 / §6.18。
 *
 * 阶段 2(MES-24):本地持久化 + 服务端同步双轨。
 * - 写入时本地状态立即生效(乐观更新),同时 fire-and-forget 同步到
 *   `PATCH /api/v1/users/me`(auth.md §3.1,键级浅合并);
 * - 422 unsupported_locale / 422 invalid_timezone 经 lastSyncError 上报,
 *   供 UI 层按 error.code 渲染 i18n 错误提示(§6.14 具名 code);
 * - 网络错误/服务端错误静默降级,本地持久化作为降级镜像继续可用。
 *
 * 本地持久化键:
 * - `mesh.settings.v1`(zustand persist,完整偏好)
 * - `mesh.theme`(镜像主题模式 'light|dark|system',供 index.html 内联防闪烁脚本同步读取)
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { MeshApiClient } from '../api/client';
import type { PreferenceSyncError } from './preferencesSync';
import {
  syncLocaleToServer,
  syncPreferencesToServer,
  syncThemeToServer,
  syncTimezoneToServer,
} from './preferencesSync';

export type ThemeMode = 'light' | 'dark' | 'system';

export interface UserPreferences {
  theme: ThemeMode;
  /** BCP-47;null = 未单独设置,协商链落到工作区默认(§6.18) */
  locale: string | null;
  /** IANA 时区,仅展示层(存储恒 UTC,§6.18) */
  timezone: string;
}

export interface SettingsState {
  preferences: UserPreferences;
  /** 最近一次服务端同步错误(null = 无错误);UI 层读取后渲染提示 */
  lastSyncError: PreferenceSyncError | null;
  setTheme: (theme: ThemeMode) => void;
  setLocale: (locale: string | null) => void;
  setTimezone: (timezone: string) => void;
  resetPreferences: () => void;
  /** 清除同步错误状态(UI 关闭提示后调用) */
  clearSyncError: () => void;
}

export const SETTINGS_STORAGE_KEY = 'mesh.settings.v1';
export const THEME_MIRROR_KEY = 'mesh.theme';

export function detectTimezone(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return tz && tz.length > 0 ? tz : 'UTC';
  } catch {
    return 'UTC';
  }
}

export function defaultPreferences(): UserPreferences {
  return { theme: 'system', locale: null, timezone: detectTimezone() };
}

function mirrorTheme(theme: ThemeMode): void {
  try {
    localStorage.setItem(THEME_MIRROR_KEY, theme);
  } catch {
    /* 存储不可用时仅内存态生效 */
  }
}

/**
 * 服务端同步客户端注入点。
 * 由 App 层在挂载时调用 `bindSyncClient(getApiClient())` 激活同步;
 * 未绑定前所有 setter 仅更新本地(骨架行为不变,测试无需真实网络)。
 */
let syncClient: MeshApiClient | null = null;

/** 绑定服务端同步客户端(应用启动时调用一次) */
export function bindSyncClient(client: MeshApiClient): void {
  syncClient = client;
}

/** 解绑同步客户端(仅测试用) */
export function unbindSyncClient(): void {
  syncClient = null;
}

/** 获取当前绑定的同步客户端(测试与内部使用) */
export function getSyncClient(): MeshApiClient | null {
  return syncClient;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      preferences: defaultPreferences(),
      lastSyncError: null,

      setTheme: (theme) => {
        set((state) => {
          mirrorTheme(theme);
          return { preferences: { ...state.preferences, theme }, lastSyncError: null };
        });
        if (syncClient !== null) {
          void syncThemeToServer(syncClient, theme, {
            onError: (err) => set({ lastSyncError: err }),
          });
        }
      },

      setLocale: (locale) => {
        set((state) => ({
          preferences: { ...state.preferences, locale },
          lastSyncError: null,
        }));
        if (syncClient !== null) {
          void syncLocaleToServer(syncClient, locale, {
            onError: (err) => set({ lastSyncError: err }),
          });
        }
      },

      setTimezone: (timezone) => {
        set((state) => ({
          preferences: { ...state.preferences, timezone },
          lastSyncError: null,
        }));
        if (syncClient !== null) {
          void syncTimezoneToServer(syncClient, timezone, {
            onError: (err) => set({ lastSyncError: err }),
          });
        }
      },

      resetPreferences: () => {
        const next = defaultPreferences();
        set(() => {
          mirrorTheme(next.theme);
          return { preferences: next, lastSyncError: null };
        });
        if (syncClient !== null) {
          void syncPreferencesToServer(syncClient, next, {
            onError: (err) => set({ lastSyncError: err }),
          });
        }
      },

      clearSyncError: () => set({ lastSyncError: null }),
    }),
    {
      name: SETTINGS_STORAGE_KEY,
      partialize: (state) => ({ preferences: state.preferences }),
      onRehydrateStorage: () => (state) => {
        if (state) mirrorTheme(state.preferences.theme);
      },
    },
  ),
);

/** 读取当前偏好(非 React 上下文,如偏好同步模块) */
export function getPreferences(): UserPreferences {
  return useSettingsStore.getState().preferences;
}

/** 读取最近同步错误(非 React 上下文) */
export function getLastSyncError(): PreferenceSyncError | null {
  return useSettingsStore.getState().lastSyncError;
}

