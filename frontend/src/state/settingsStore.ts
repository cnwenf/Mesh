/**
 * 账号级展示偏好(theme/locale/timezone)— README §6.12 / §6.18 / theme.md §2。
 *
 * 双轨:写入时本地立即生效(乐观更新)+ fire-and-forget 同步到
 * `PATCH /api/v1/users/me`(auth.md §3.1,键级浅合并);服务端为跨设备真源,
 * 本地持久化仅作降级镜像与防闪烁首帧用途(theme.md §4.5)。
 *
 * theme 三值语义写死(theme.md §2.1):
 * - `light`/`dark` = 固定深浅;
 * - `system` = **忽略工作区默认、跟随操作系统**;
 * - `null`(默认)= 未表达偏好,**继承工作区默认**(协商链跳过第 1 级)。
 * 「恢复跟随默认」实际写入 `null`(而非 "system")。
 *
 * 本地持久化键:
 * - `mesh.settings.v1`(zustand persist,v2:theme 可为 null;v1 的 'system'
 *   默认值迁移为 null——语义对齐「未表达偏好」)
 * - 首帧分区镜像 `mesh.theme.active` 由 themeLocator/ThemeProvider 维护
 *   ({id: 路由身份, mode: light|dark},theme.md §2.3 ②)。
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { MeshApiClient } from '../api/client';
import { LEGACY_THEME_MIRROR_KEY, clearThemeLocators } from '../design/themeLocator';
import { isThemeMode } from '../design/themeNegotiation';
import type { ThemeMode } from '../design/themeNegotiation';
import {
  clearPendingWritesForHost,
  setActiveUser,
  setActiveWorkspace,
} from './pendingSettingsQueue';
import type { PreferenceSyncError } from './preferencesSync';
import {
  syncLocaleToServer,
  syncPreferencesToServer,
  syncThemeToServer,
  syncTimezoneToServer,
} from './preferencesSync';

export type { ThemeMode };

export interface UserPreferences {
  /** `light|dark|system` 显式偏好;`null` = 未表达,继承工作区默认(§2.1)。 */
  theme: ThemeMode | null;
  /** BCP-47;null = 未单独设置,协商链落到工作区默认(§6.18) */
  locale: string | null;
  /** IANA 时区,仅展示层(存储恒 UTC,§6.18) */
  timezone: string;
}

/** 服务端回填快照(GET /me 解析后;服务端为跨设备真源,§4.5)。 */
export interface ServerPreferenceSnapshot {
  readonly theme?: ThemeMode | null;
  readonly locale?: string | null;
  readonly timezone?: string | null;
}

export interface SettingsState {
  preferences: UserPreferences;
  /** 最近一次服务端同步错误(null = 无错误);UI 层读取后渲染提示 */
  lastSyncError: PreferenceSyncError | null;
  /** bootstrap 是否已完成会话探测(H3 防 skeleton 死锁:匿名无 session 时不置位,
   *  使 ThemeProvider 的 skeleton 永不触发;登录/全局页探测后置 true)。非持久化。 */
  sessionProbed: boolean;
  markSessionProbed: () => void;
  setTheme: (theme: ThemeMode | null) => void;
  setLocale: (locale: string | null) => void;
  setTimezone: (timezone: string) => void;
  resetPreferences: () => void;
  /** 登录/启动时以服务端值回填(服务端覆盖本地同名镜像,§4.5 裁决) */
  hydrateFromServer: (remote: ServerPreferenceSnapshot) => void;
  /** 清除同步错误状态(UI 关闭提示后调用) */
  clearSyncError: () => void;
}

export const SETTINGS_STORAGE_KEY = 'mesh.settings.v1';

export function detectTimezone(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return tz && tz.length > 0 ? tz : 'UTC';
  } catch {
    return 'UTC';
  }
}

export function defaultPreferences(): UserPreferences {
  // theme 默认 absent/null = 继承工作区默认(协商链第 2 级,§2.1)。
  return { theme: null, locale: null, timezone: detectTimezone() };
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
      sessionProbed: false,

      markSessionProbed: () => set({ sessionProbed: true }),

      setTheme: (theme) => {
        set((state) => ({
          preferences: { ...state.preferences, theme },
          lastSyncError: null,
        }));
        if (syncClient !== null) {
          // null = 显式清除、恢复跟随工作区默认(§3.2 PATCH {theme: null})。
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
        set(() => ({ preferences: next, lastSyncError: null }));
        if (syncClient !== null) {
          void syncPreferencesToServer(syncClient, next, {
            onError: (err) => set({ lastSyncError: err }),
          });
        }
      },

      hydrateFromServer: (remote) => {
        // §4.5 裁决:服务端有值 → 覆盖本地同名镜像;absent/null → 偏好置
        // null(匿名阶段本地值不充当账号偏好;协商链自工作区默认起解析,
        // 本地镜像仅作 §2.3 ② 防闪烁用途,由 locator 自管)。
        set((state) => ({
          preferences: {
            theme: isThemeMode(remote.theme) ? remote.theme : null,
            locale:
              remote.locale !== undefined ? (remote.locale ?? null) : state.preferences.locale,
            timezone:
              typeof remote.timezone === 'string' && remote.timezone.length > 0
                ? remote.timezone
                : state.preferences.timezone,
          },
          lastSyncError: null,
        }));
      },

      clearSyncError: () => set({ lastSyncError: null }),
    }),
    {
      name: SETTINGS_STORAGE_KEY,
      version: 2,
      migrate: (persistedState, version) => {
        const state = persistedState as { preferences?: { theme?: unknown } };
        if (version < 2 && state?.preferences) {
          // v1 默认 'system' ≈ 未表达偏好 → null(协商链自工作区默认起);
          // 显式 light/dark 保留。
          const theme = state.preferences.theme;
          state.preferences.theme = theme === 'light' || theme === 'dark' ? theme : null;
        }
        return state;
      },
      partialize: (state) => ({ preferences: state.preferences }),
      onRehydrateStorage: () => () => {
        // 一次性清理阶段 2 遗留镜像键(防闪烁改由分区 locator 承载)。
        try {
          localStorage.removeItem(LEGACY_THEME_MIRROR_KEY);
        } catch {
          /* 存储不可用时本就没有遗留键 */
        }
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

let crossTabTeardown: (() => void) | null = null;

/**
 * 跨标签页同步(§4.2 评审 T5② 补齐):zustand persist 默认不监听同源其他
 * 标签页的写入——注册 storage 监听,偏好写入即时同步到当前标签(不刷新、
 * 不二次回推服务端)。locator 键的跨标签页变更无需事件中转:各标签页在自身
 * 解析落地时按当前路由身份回写自己的 locator,ThemeProvider 的权威解析
 * effect 即重校验来源(评审 L2/B3:原 LOCATOR_CHANGE_EVENT 无生产消费者,已删)。
 * 返回拆卸函数;重复调用幂等(仅首个生效)。
 */
export function initCrossTabSync(): () => void {
  if (crossTabTeardown !== null || typeof window === 'undefined') {
    return () => {};
  }
  const onStorage = (event: StorageEvent): void => {
    if (event.key === SETTINGS_STORAGE_KEY && event.newValue !== null) {
      try {
        const parsed = JSON.parse(event.newValue) as {
          state?: { preferences?: Partial<UserPreferences> };
        };
        const prefs = parsed?.state?.preferences;
        if (prefs !== null && typeof prefs === 'object') {
          useSettingsStore.setState((current) => ({
            preferences: {
              theme: isThemeMode(prefs.theme) ? prefs.theme : null,
              locale: typeof prefs.locale === 'string' ? prefs.locale : null,
              timezone:
                typeof prefs.timezone === 'string' && prefs.timezone.length > 0
                  ? prefs.timezone
                  : current.preferences.timezone,
            },
          }));
        }
      } catch {
        /* 其他标签页写入了非法载荷:忽略,保持本标签现状 */
      }
    }
  };
  window.addEventListener('storage', onStorage);
  crossTabTeardown = () => {
    window.removeEventListener('storage', onStorage);
    crossTabTeardown = null;
  };
  return crossTabTeardown;
}

/**
 * 登出清理(theme.md §2.3):删除分区 locator + 遗留镜像键 + 当前 host 下
 * pending 偏好队列分区,防下一账号串用;偏好回到「未表达」(theme=null,
 * 协商链自工作区默认起),不触发服务端同步。
 */
export function onLogoutCleanup(): void {
  clearThemeLocators();
  clearPendingWritesForHost();
  setActiveUser(null);
  setActiveWorkspace(null);
  useSettingsStore.setState((state) => ({
    preferences: { ...state.preferences, theme: null, locale: null },
    lastSyncError: null,
    sessionProbed: false,
  }));
}
