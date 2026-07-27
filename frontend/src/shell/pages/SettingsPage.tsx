/**
 * 设置页(README §6.12 主题契约 / §6.18 locale 与时区化):
 * - 外观:theme light/dark/system,即时切换无刷新(ThemeProvider 落 <html data-theme>);
 * - 语言:首项「跟随工作区默认」(值 ''→setLocale(null)),其余为 SUPPORTED_LOCALES;
 * - 时区:候选 + 当前检测时区去重;helper 文案标注浏览器默认;
 * - tz-sample 以 formatWithZoneAnnotation 渲染固定 UTC 时刻,切换时区/语言即时更新;
 * - 偏好同步错误(MES-24):422 unsupported_locale/invalid_timezone 等经
 *   lastSyncError 消费,按 error code 渲染 i18n 错误文案 + 可关闭(§6.14/§6.18)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchMe } from '../../api/auth';
import type { CurrentUser } from '../../api/auth';
import { getApiClient } from '../../api/instance';
import { Banner, Select } from '../../design';
import { SecuritySettings } from '../../features/auth';
import { formatWithZoneAnnotation, SUPPORTED_LOCALES, useT } from '../../i18n';
import { useSettingsStore } from '../../state/settingsStore';
import type { ThemeMode } from '../../state/settingsStore';
import type { PreferenceSyncError } from '../../state/preferencesSync';

const BASE_TIMEZONES: ReadonlyArray<string> = [
  'UTC',
  'Asia/Shanghai',
  'America/New_York',
  'Europe/London',
];

const SAMPLE_INSTANT = '2026-07-25T18:00:00Z';

const THEME_OPTIONS: ReadonlyArray<ThemeMode> = ['light', 'dark', 'system'];

/** 将 PreferenceSyncError 映射为 i18n 消息键(§6.14 具名 code → 前端渲染) */
function syncErrorToI18nKey(error: PreferenceSyncError): string {
  if (error.code === 'unsupported_locale' || error.code === 'invalid_timezone') {
    return `error.${error.code}`;
  }
  if (error.code === 'network') {
    return 'error.network';
  }
  return 'settings.syncErrorServer';
}

export function SettingsPage(): React.JSX.Element {
  const t = useT();
  const preferences = useSettingsStore((state) => state.preferences);
  const setTheme = useSettingsStore((state) => state.setTheme);
  const setLocale = useSettingsStore((state) => state.setLocale);
  const setTimezone = useSettingsStore((state) => state.setTimezone);
  const lastSyncError = useSettingsStore((state) => state.lastSyncError);
  const clearSyncError = useSettingsStore((state) => state.clearSyncError);

  // 当前用户(供安全设置:会话/两步验证/第三方绑定)。未登录时不渲染安全区。
  const [user, setUser] = useState<CurrentUser | null>(null);
  const client = getApiClient();
  // 卸载守卫:fetchMe 在卸载后才落定时不得再 setState(调度竞态下会向已拆除
  // 的渲染树派发更新,测试环境 teardown 后表现为 unhandled rejection)。
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);
  const reloadUser = useCallback(() => {
    void fetchMe(client)
      .then((me) => {
        if (isMountedRef.current) setUser(me);
      })
      .catch(() => {
        if (isMountedRef.current) setUser(null);
      });
  }, [client]);
  useEffect(() => {
    reloadUser();
  }, [reloadUser]);

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

  return (
    <div className="mesh-page">
      <h1 className="mesh-page__title">{t('settings.title')}</h1>

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

      <section className="mesh-settings__section" aria-label={t('settings.appearance')}>
        <h2 className="mesh-settings__heading">{t('settings.appearance')}</h2>
        <Select
          data-testid="theme-select"
          label={t('theme.label')}
          value={preferences.theme}
          onChange={(event) => setTheme(event.target.value as ThemeMode)}
        >
          {THEME_OPTIONS.map((mode) => (
            <option key={mode} value={mode}>
              {t('theme.' + mode)}
            </option>
          ))}
        </Select>
      </section>

      <section className="mesh-settings__section" aria-label={t('settings.language')}>
        <h2 className="mesh-settings__heading">{t('settings.language')}</h2>
        <Select
          data-testid="locale-select"
          label={t('settings.language')}
          value={preferences.locale ?? ''}
          onChange={(event) => setLocale(event.target.value === '' ? null : event.target.value)}
        >
          <option value="">{t('settings.languageFollowDefault')}</option>
          {SUPPORTED_LOCALES.map((locale) => (
            <option key={locale} value={locale}>
              {locale}
            </option>
          ))}
        </Select>
      </section>

      <section className="mesh-settings__section" aria-label={t('settings.timezone')}>
        <h2 className="mesh-settings__heading">{t('settings.timezone')}</h2>
        <Select
          data-testid="timezone-select"
          label={t('settings.timezone')}
          value={preferences.timezone}
          onChange={(event) => setTimezone(event.target.value)}
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
      </section>

      {/* auth.md §4.2 安全:会话 / 两步验证 / 第三方绑定(仅登录态) */}
      {user !== null ? (
        <section className="mesh-settings__section" aria-label={t('security.title')}>
          <h2 className="mesh-settings__heading">{t('security.title')}</h2>
          <SecuritySettings client={client} user={user} onUserChanged={reloadUser} />
        </section>
      ) : null}
    </div>
  );
}
