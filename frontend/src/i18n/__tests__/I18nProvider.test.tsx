/**
 * I18nProvider / useT 测试 — i18n.md §4.2(切换即时生效无刷新)/§4.5(开发期可见标记)。
 */
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSettingsStore } from '../../state/settingsStore';
import { I18nProvider, createIntlErrorHandler, extractMissingKey, useT } from '../I18nProvider';
import type { MissingEntry, MissingReporter } from '../missing';

interface ImportMetaWithDev {
  DEV: boolean;
}

function createTestReporter(): { reporter: MissingReporter; report: ReturnType<typeof vi.fn> } {
  const report = vi.fn();
  return { reporter: { report, reported: [] as ReadonlyArray<MissingEntry> }, report };
}

interface ProbeProps {
  messageKey: string;
  values?: Record<string, unknown>;
}

function Probe({ messageKey, values }: ProbeProps) {
  const t = useT();
  return (
    <div>
      <span data-testid="anchor" />
      <span data-testid="msg">{t(messageKey, values)}</span>
    </div>
  );
}

describe('I18nProvider + useT(§6.18 协商链接线 + §4.2 即时切换)', () => {
  let originalDev: boolean;

  beforeEach(() => {
    originalDev = import.meta.env.DEV;
    act(() => useSettingsStore.getState().resetPreferences());
  });

  afterEach(() => {
    (import.meta.env as unknown as ImportMetaWithDev).DEV = originalDev;
  });

  it('未设置账号偏好且无工作区默认 → 回退 en', () => {
    render(
      <I18nProvider>
        <Probe messageKey="common.save" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('msg')).toHaveTextContent('Save');
  });

  it('工作区默认 zh-CN 生效(账号偏好为 null 时跳过本级)', () => {
    render(
      <I18nProvider workspaceDefaultLocale="zh-CN">
        <Probe messageKey="common.save" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('msg')).toHaveTextContent('保存');
  });

  it('账号偏好优先于工作区默认', () => {
    act(() => useSettingsStore.getState().setLocale('en'));
    render(
      <I18nProvider workspaceDefaultLocale="zh-CN">
        <Probe messageKey="common.save" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('msg')).toHaveTextContent('Save');
  });

  it('请求显式参数级(?locale= / Accept-Language 等价物)优先于账号偏好与工作区默认', () => {
    act(() => useSettingsStore.getState().setLocale('en'));
    render(
      <I18nProvider requested={['zh-CN']} workspaceDefaultLocale="en">
        <Probe messageKey="common.save" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('msg')).toHaveTextContent('保存');
  });

  it('requested 候选含不支持值时按协商规则忽略,落到后续级别', () => {
    render(
      <I18nProvider requested={['xx-YY', 'zh-CN']}>
        <Probe messageKey="common.save" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('msg')).toHaveTextContent('保存');
  });

  it('requested 为字符串(单值)时同样生效', () => {
    render(
      <I18nProvider requested="zh-CN">
        <Probe messageKey="common.save" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('msg')).toHaveTextContent('保存');
  });

  it('settingsStore 偏好变化 → 文案就地更新,组件不重挂载(§4.2 无刷新)', () => {
    render(
      <I18nProvider>
        <Probe messageKey="common.save" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('msg')).toHaveTextContent('Save');
    const anchorBefore = screen.getByTestId('anchor');

    act(() => useSettingsStore.getState().setLocale('zh-CN'));
    expect(screen.getByTestId('msg')).toHaveTextContent('保存');
    expect(screen.getByTestId('anchor')).toBe(anchorBefore);

    act(() => useSettingsStore.getState().setLocale('en'));
    expect(screen.getByTestId('msg')).toHaveTextContent('Save');
    expect(screen.getByTestId('anchor')).toBe(anchorBefore);
  });

  it('ICU 参数经 Provider 按 locale 填充并随切换就地更新(§2.4/§4.2)', () => {
    // 目录内带参消息(home.greeting)经 useT 渲染:en/zh 各自模板 + 参数插值。
    act(() => useSettingsStore.getState().setLocale('en'));
    render(
      <I18nProvider>
        <Probe messageKey="home.greeting" values={{ name: 'Jane' }} />
      </I18nProvider>,
    );
    expect(screen.getByTestId('msg')).toHaveTextContent('Welcome back, Jane');

    act(() => useSettingsStore.getState().setLocale('zh-CN'));
    expect(screen.getByTestId('msg')).toHaveTextContent('欢迎回来,Jane');
  });

  it('ICU date/number 分支渲染经目录键覆盖(catalogs 内联 createIntl 逐 locale 校验)', () => {
    // Provider 的 defaultMessage 回退恒按 en 编译(react-intl 契约),locale 化
    // ICU(plural/date/number)的正确性由 catalogs.test 的 createIntl 逐 locale
    // 断言覆盖;此处仅验证目录内带 ICU 占位的消息经 Provider 正常渲染不塌缩。
    act(() => useSettingsStore.getState().setLocale('en'));
    render(
      <I18nProvider>
        <Probe messageKey="home.dashboardTitle" values={{ workspace: 'Acme' }} />
      </I18nProvider>,
    );
    expect(screen.getByTestId('msg')).toHaveTextContent('Acme');
  });

  it('缺 key:开发期呈现 ⚠[key] 可见标记并上报(§4.5)', () => {
    const { reporter, report } = createTestReporter();
    render(
      <I18nProvider reporter={reporter}>
        <Probe messageKey="no.such.key" />
      </I18nProvider>,
    );
    const msg = screen.getByTestId('msg').textContent ?? '';
    expect(msg).toContain('⚠[no.such.key]');
    expect(msg).toContain('no.such.key');
    expect(report).toHaveBeenCalledWith('en', 'no.such.key', 'key');
  });

  it('生产构建关闭可见标记,仅呈现回退文案(§4.5)', () => {
    (import.meta.env as unknown as ImportMetaWithDev).DEV = false;
    const { reporter } = createTestReporter();
    render(
      <I18nProvider reporter={reporter}>
        <Probe messageKey="no.such.key" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('msg')).toHaveTextContent('no.such.key');
    expect(screen.getByTestId('msg').textContent).not.toContain('⚠');
  });

  it('缺 key 命中回退不中断整页,其余文案正常渲染(§2.5 单点回退)', () => {
    const { reporter } = createTestReporter();
    function Mixed() {
      const t = useT();
      return (
        <div>
          <span data-testid="good">{t('common.save')}</span>
          <span data-testid="bad">{t('missing.key')}</span>
        </div>
      );
    }
    render(
      <I18nProvider reporter={reporter}>
        <Mixed />
      </I18nProvider>,
    );
    expect(screen.getByTestId('good')).toHaveTextContent('Save');
    expect(screen.getByTestId('bad')).toHaveTextContent('missing.key');
  });

  it('intl 错误经 onError 静默处理,无 console 噪声(§4.5)', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const { reporter } = createTestReporter();
    render(
      <I18nProvider reporter={reporter}>
        <Probe messageKey="missing.key" />
      </I18nProvider>,
    );
    expect(errorSpy).not.toHaveBeenCalled();
    expect(warnSpy).not.toHaveBeenCalled();
  });

  it('useT 脱离 I18nProvider 使用 → 抛清晰错误(显式失败,不静默)', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => render(<Probe messageKey="common.save" />)).toThrow(/I18nProvider/);
  });
});

describe('intl 错误处理纯函数(§4.5:onError 仅报 missing,其余静默)', () => {
  it('extractMissingKey:从 MISSING_TRANSLATION 文案提取 key;格式不符回退 unknown', () => {
    expect(
      extractMissingKey(
        'Missing message: "issue.create.title" for locale "zh-CN", using default message',
      ),
    ).toBe('issue.create.title');
    expect(extractMissingKey('some other intl error text')).toBe('unknown');
  });

  it('createIntlErrorHandler:MISSING_TRANSLATION 上报;其余错误码静默不报', () => {
    const { reporter, report } = createTestReporter();
    const handler = createIntlErrorHandler(reporter, 'zh-CN');

    const missing = Object.assign(new Error('Missing message: "a.b" for locale "zh-CN"'), {
      code: 'MISSING_TRANSLATION',
    });
    handler(missing);
    expect(report).toHaveBeenCalledWith('zh-CN', 'a.b', 'key');

    handler(Object.assign(new Error('boom'), { code: 'FORMAT_ERROR' }));
    handler(new Error('no code at all'));
    expect(report).toHaveBeenCalledTimes(1);
  });
});
