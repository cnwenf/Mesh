/**
 * 共享测试渲染工具 — 为 shell/页面测试提供与生产一致的 Provider 栈。
 * MemoryRouter + ThemeProvider + I18nProvider + ToastProvider。
 * I18nProvider 注入静默缺失上报器,测试不触网。
 */
/* eslint-disable react-refresh/only-export-components -- 测试工具:渲染函数与内部 Provider 组件同文件共存 */
import type { ReactElement, ReactNode } from 'react';
import { render } from '@testing-library/react';
import type { RenderResult } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider, ToastProvider } from '../design';
import { I18nProvider, useT } from '../i18n';
import type { MissingReporter } from '../i18n';

const silentReporter: MissingReporter = { report: () => undefined, reported: [] };

export interface RenderWithProvidersOptions {
  /** 初始路由(MemoryRouter initialEntries) */
  route?: string;
  /** route 的别名(二者择一,route 优先) */
  path?: string;
}

/** ToastProvider 需要经 useT 提供 regionLabel,故单独包一层(位于 I18nProvider 内)。 */
function ToastLayer(props: { children: ReactNode }): React.JSX.Element {
  const t = useT();
  return <ToastProvider regionLabel={t('a11y.notifications')}>{props.children}</ToastProvider>;
}

export function renderWithProviders(
  ui: ReactElement,
  opts: RenderWithProvidersOptions = {},
): RenderResult {
  const initialPath = opts.route ?? opts.path ?? '/';
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <ThemeProvider>
        <I18nProvider workspaceDefaultLocale={null} reporter={silentReporter}>
          <ToastLayer>{ui}</ToastLayer>
        </I18nProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}
