/**
 * I18nProvider / useT — i18n.md §4.2(locale 切换即时生效,无刷新)/§4.5(开发期可见标记)。
 *
 * - locale 经 negotiateLocale 协商(§6.18):账号偏好(settingsStore,响应式)
 *   → 工作区默认(prop)→ 回退 en;协商结果必在内置目录清单内(negotiate 契约保证);
 * - react-intl IntlProvider 接线内置目录(同步加载,离线可用);
 * - useT:resolveMessage 三级回退 + 缺失上报;开发期对回退文案加 `⚠[key]` 前缀,
 *   生产构建为纯回退文案;
 * - onError 仅对 MISSING_TRANSLATION 上报,其余 intl 错误静默(无 console 噪声)。
 */
/* eslint-disable react-refresh/only-export-components -- 模块契约:Provider / hook / 错误处理同文件共存 */
import type { JSX, ReactNode } from 'react';
import { createContext, useCallback, useContext, useMemo } from 'react';
import { IntlProvider, useIntl } from 'react-intl';
import { useSettingsStore } from '../state/settingsStore';
import { builtinCatalogs, resolveMessage } from './catalogLoader';
import { createMissingReporter } from './missing';
import type { MissingReporter } from './missing';
import { FALLBACK_LOCALE, negotiateLocale } from './negotiate';

/** 模块级默认上报器(开发期启用);可经 I18nProvider 的 reporter 属性覆盖。 */
export const defaultMissingReporter: MissingReporter = createMissingReporter();

interface I18nContextValue {
  readonly reporter: MissingReporter;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export interface I18nProviderProps {
  readonly children: ReactNode;
  /**
   * 协商链"请求显式参数"级(§6.18):?locale= / Accept-Language 等价物
   * (浏览器环境为 navigator.languages)。BCP-47 候选数组,非法值被协商忽略。
   */
  readonly requested?: string | readonly string[] | null;
  /**
   * workspaces.settings.default_locale(协商链"工作区默认"级)。
   * 骨架阶段工作区 API 未落地(阶段 2),由调用方传 null;协商自动落到系统回退 en。
   */
  readonly workspaceDefaultLocale?: string | null;
  /**
   * 系统级候选(浏览器 navigator.languages):工作区默认之后、en 回退之前尝试。
   * 账号偏好/工作区默认优先于浏览器语言(否则账号级偏好永不生效,i18n.md L1)。
   */
  readonly systemLocales?: readonly string[] | null;
  /** 缺失上报器注入(测试与自定义上报策略) */
  readonly reporter?: MissingReporter;
}

/** react-intl MISSING_TRANSLATION 错误码 */
const MISSING_TRANSLATION = 'MISSING_TRANSLATION';
const MISSING_MESSAGE_KEY_PATTERN = /Missing message: "([^"]+)"/;
const UNKNOWN_KEY = 'unknown';

/** 从 MISSING_TRANSLATION 错误文案提取缺 key;格式不符时回退 'unknown'。 */
export function extractMissingKey(errorMessage: string): string {
  return MISSING_MESSAGE_KEY_PATTERN.exec(errorMessage)?.[1] ?? UNKNOWN_KEY;
}

/**
 * intl onError 处理器:MISSING_TRANSLATION 上报缺 key;
 * 其余 intl 错误一律静默,不制造 console 噪声(§4.5)。
 */
export function createIntlErrorHandler(
  reporter: MissingReporter,
  locale: string,
): (error: Error) => void {
  return (error: Error): void => {
    const code = (error as Error & { code?: string }).code;
    if (code !== MISSING_TRANSLATION) return;
    reporter.report(locale, extractMissingKey(error.message), 'key');
  };
}

export function I18nProvider(props: I18nProviderProps): JSX.Element {
  const userLocale = useSettingsStore((state) => state.preferences.locale);
  const reporter = props.reporter ?? defaultMissingReporter;
  const contextValue = useMemo<I18nContextValue>(() => ({ reporter }), [reporter]);
  const locale = useMemo(
    () =>
      negotiateLocale({
        requested: props.requested ?? null,
        userLocale,
        workspaceDefaultLocale: props.workspaceDefaultLocale,
        systemLocales: props.systemLocales ?? null,
      }),
    [props.requested, userLocale, props.workspaceDefaultLocale, props.systemLocales],
  );
  // 不变式:negotiateLocale 的返回值必在受支持清单内,内置目录静态包含全部受支持
  // locale(含回退 en),故 builtinCatalogs[locale] 必然存在(negotiate/catalogs 测试保证)。
  const catalog = builtinCatalogs[locale];
  const handleIntlError = useMemo(
    () => createIntlErrorHandler(reporter, locale),
    [reporter, locale],
  );

  return (
    <I18nContext.Provider value={contextValue}>
      <IntlProvider
        locale={locale}
        defaultLocale={FALLBACK_LOCALE}
        messages={catalog.messages}
        onError={handleIntlError}
      >
        {props.children}
      </IntlProvider>
    </I18nContext.Provider>
  );
}

/** ICU 占位符可接受的结构化值类型(与 react-intl PrimitiveType 对齐) */
type MessageValue = string | number | boolean | Date | null | undefined;

export type TranslateFn = (key: string, values?: Record<string, unknown>) => string;

/**
 * 取本地化文案。命中回退链时上报缺失;开发期文案加 `⚠[key]` 前缀(§4.5),
 * 生产构建输出纯回退文案。values 以结构化参数填充 ICU 占位符(§2.4)。
 * 必须在 <I18nProvider> 内使用,否则显式抛错(不静默)。
 */
export function useT(): TranslateFn {
  const context = useContext(I18nContext);
  if (context === null) {
    throw new Error('useT must be used within an <I18nProvider>');
  }
  const intl = useIntl();
  const reporter = context.reporter;
  const locale = intl.locale;
  return useCallback<TranslateFn>(
    (key, values) => {
      const resolved = resolveMessage(
        { primary: builtinCatalogs[locale], fallback: builtinCatalogs[FALLBACK_LOCALE] },
        key,
      );
      if (resolved.fallback !== 'none') {
        reporter.report(locale, key, resolved.fallback);
      }
      const showDevMarker = import.meta.env.DEV && resolved.fallback !== 'none';
      const defaultMessage = showDevMarker ? `⚠[${key}] ${resolved.text}` : resolved.text;
      return intl.formatMessage(
        { id: key, defaultMessage },
        values as Record<string, MessageValue>,
      );
    },
    [intl, locale, reporter],
  );
}
