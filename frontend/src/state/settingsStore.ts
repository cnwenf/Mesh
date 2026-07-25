/**
 * 账号级展示偏好(theme/locale/timezone)— README §6.12 / §6.18。
 *
 * 骨架阶段本地持久化(localStorage);阶段 2 经 auth.md `PATCH /api/v1/users/me`
 * 写入 `users.settings`(locale/theme)与 `users.timezone`(IANA)。
 * 本地持久化键:
 * - `mesh.settings.v1`(zustand persist,完整偏好)
 * - `mesh.theme`(镜像主题模式 'light|dark|system',供 index.html 内联防闪烁脚本同步读取)
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

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
  setTheme: (theme: ThemeMode) => void;
  setLocale: (locale: string | null) => void;
  setTimezone: (timezone: string) => void;
  resetPreferences: () => void;
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

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      preferences: defaultPreferences(),
      setTheme: (theme) =>
        set((state) => {
          mirrorTheme(theme);
          return { preferences: { ...state.preferences, theme } };
        }),
      setLocale: (locale) =>
        set((state) => ({ preferences: { ...state.preferences, locale } })),
      setTimezone: (timezone) =>
        set((state) => ({ preferences: { ...state.preferences, timezone } })),
      resetPreferences: () =>
        set(() => {
          const next = defaultPreferences();
          mirrorTheme(next.theme);
          return { preferences: next };
        }),
    }),
    {
      name: SETTINGS_STORAGE_KEY,
      onRehydrateStorage: () => (state) => {
        if (state) mirrorTheme(state.preferences.theme);
      },
    },
  ),
);
