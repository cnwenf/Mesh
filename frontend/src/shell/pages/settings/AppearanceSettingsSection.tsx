/**
 * 账号设置 → 外观(/settings/appearance,theme.md §4.1 / README §6.12 / §6.18)。
 *
 * - 主题三态:light/dark/system + 首项「跟随默认(X)」(写 null = 继承工作区默认,§4.1);
 *   system 选项标注系统当前解析值;两级关系可视化经占位标注当前解析值。
 * - 语言:首项「跟随工作区默认」(值 ''→setLocale(null)),其余 SUPPORTED_LOCALES。
 * - 时区:候选 + 当前检测时区去重;tz-sample 以 formatWithZoneAnnotation 即时更新。
 * - 即时生效控件不带保存按钮;切换即本地应用 + 成功 toast,偏好同步错误经 lastSyncError
 *   横幅具名呈现(MES-24,§6.14)。
 */
import { useEffect, useMemo, useState } from 'react';
import { Banner, Select, SettingsSection, useToast } from '../../../design';
import { formatWithZoneAnnotation, SUPPORTED_LOCALES, useT } from '../../../i18n';
import { useSettingsStore } from '../../../state/settingsStore';
import type { ThemeMode } from '../../../state/settingsStore';
import type { PreferenceSyncError } from '../../../state/preferencesSync';
import { useWorkspaceThemeBridge } from '../../../state/workspaceThemeBridge';
import { resolveThemeChain } from '../../../design/themeNegotiation';
import type { ResolvedTheme } from '../../../design/themeNegotiation';

const BASE_TIMEZONES: ReadonlyArray<string> = [
  'UTC',
  'Asia/Shanghai',
  'America/New_York',
  'Europe/London',
];

const SAMPLE_INSTANT = '2026-07-25T18:00:00Z';

/** 将 PreferenceSyncError 映射为 i18n 消息键(§6.14 具名 code → 前端渲染)。 */
export function syncErrorToI18nKey(error: PreferenceSyncError): string {
  if (
    error.code === 'unsupported_locale' ||
    error.code === 'invalid_timezone' ||
    error.code === 'invalid_theme_mode'
  ) {
    return `error.${error.code}`;
  }
  if (error.code === 'network') {
    return 'error.network';
  }
  return 'settings.syncErrorServer';
}

export function AppearanceSettingsSection(): React.JSX.Element {
  const t = useT();
  const { addToast } = useToast();
  const preferences = useSettingsStore((state) => state.preferences);
  const setTheme = useSettingsStore((state) => state.setTheme);
  const setLocale = useSettingsStore((state) => state.setLocale);
  const setTimezone = useSettingsStore((state) => state.setTimezone);
  const lastSyncError = useSettingsStore((state) => state.lastSyncError);
  const clearSyncError = useSettingsStore((state) => state.clearSyncError);

  // 占位标注「跟随默认(X)」/「跟随系统(X)」需当前解析值(§4.1):全局路由无工作区
  // 上下文,未设偏好时协商链落系统级(theme.md §2.2)。
  const workspaceDefault = useWorkspaceThemeBridge((state) => state.defaultTheme);
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  );
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (event: MediaQueryListEvent): void => setSystemDark(event.matches);
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);
  const defaultResolved: ResolvedTheme = resolveThemeChain({
    userTheme: null,
    workspaceDefault,
    systemPrefersDark: systemDark,
  }).mode;
  const systemResolved: ResolvedTheme = systemDark ? 'dark' : 'light';

  const timezoneOptions = useMemo(() => {
    const options = [...BASE_TIMEZONES];
    if (!options.includes(preferences.timezone)) {
      options.push(preferences.timezone);
    }
    return options;
  }, [preferences.timezone]);

  const activeLocale = preferences.locale ?? 'en';
  const zoneSample = formatWithZoneAnnotation(SAMPLE_INSTANT, {
    locale: activeLocale,
    timeZone: preferences.timezone,
  });

  const savedToast = (): void => {
    addToast(t('settings.savedToast'), { tone: 'success', closeLabel: t('a11y.dismiss') });
  };

  return (
    <>
      {lastSyncError !== null && (
        <Banner
          tone="danger"
          politeness="assertive"
          onDismiss={clearSyncError}
          dismissLabel={t('settings.syncErrorDismiss')}
        >
          {t(syncErrorToI18nKey(lastSyncError))}
        </Banner>
      )}

      <SettingsSection title={t('settings.appearance')}>
        <Select
          data-testid="theme-select"
          label={t('theme.label')}
          value={preferences.theme ?? ''}
          onChange={(event) => {
            setTheme(event.target.value === '' ? null : (event.target.value as ThemeMode));
            savedToast();
          }}
        >
          <option value="">
            {t('theme.followDefault', { theme: t('theme.' + defaultResolved) })}
          </option>
          <option value="light">{t('theme.light')}</option>
          <option value="dark">{t('theme.dark')}</option>
          <option value="system">
            {t('theme.systemResolved', { theme: t('theme.' + systemResolved) })}
          </option>
        </Select>
        <p className="mesh-settings__hint">{t('theme.defaultHint')}</p>
      </SettingsSection>

      <SettingsSection title={t('settings.language')}>
        <Select
          data-testid="locale-select"
          label={t('settings.language')}
          value={preferences.locale ?? ''}
          onChange={(event) => {
            setLocale(event.target.value === '' ? null : event.target.value);
            savedToast();
          }}
        >
          <option value="">{t('settings.languageFollowDefault')}</option>
          {SUPPORTED_LOCALES.map((locale) => (
            <option key={locale} value={locale}>
              {locale}
            </option>
          ))}
        </Select>
      </SettingsSection>

      <SettingsSection title={t('settings.timezone')}>
        <Select
          data-testid="timezone-select"
          label={t('settings.timezone')}
          value={preferences.timezone}
          onChange={(event) => {
            setTimezone(event.target.value);
            savedToast();
          }}
        >
          {timezoneOptions.map((timezone) => (
            <option key={timezone} value={timezone}>
              {timezone}
            </option>
          ))}
        </Select>
        <p className="mesh-settings__hint">{t('settings.timezoneBrowser')}</p>
        <p className="mesh-settings__sample" data-testid="tz-sample">
          {zoneSample}
        </p>
      </SettingsSection>
    </>
  );
}
