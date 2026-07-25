/**
 * 主题切换契约(README §6.12):light / dark / system。
 *
 * - 读 settingsStore.preferences.theme(阶段 2 接通 `PATCH /api/v1/users/me`);
 * - 落到 `<html data-theme="light|dark">`,tokens-dark.css 以属性选择器整组替换语义 token;
 * - system 模式监听 `prefers-color-scheme` 实时变化,卸载时注销;
 * - 切换即时无刷新;`mesh.theme` 镜像键由 store 负责(防闪烁脚本读它),此处不重复。
 */
import { useEffect } from 'react';
import type { ReactNode } from 'react';
import type { ThemeMode } from '../state/settingsStore';
import { useSettingsStore } from '../state/settingsStore';

const DARK_SCHEME_QUERY = '(prefers-color-scheme: dark)';

/** 将主题模式解析为实际应用的主题(system 跟随系统偏好)。 */
export function resolveTheme(mode: ThemeMode, systemPrefersDark: boolean): 'light' | 'dark' {
  if (mode === 'system') {
    return systemPrefersDark ? 'dark' : 'light';
  }
  return mode;
}

export function ThemeProvider(props: { children: ReactNode }): React.JSX.Element {
  const theme = useSettingsStore((state) => state.preferences.theme);

  useEffect(() => {
    const media = window.matchMedia(DARK_SCHEME_QUERY);
    const apply = (): void => {
      document.documentElement.dataset.theme = resolveTheme(theme, media.matches);
    };
    apply();
    const handleChange = (): void => apply();
    media.addEventListener('change', handleChange);
    return () => media.removeEventListener('change', handleChange);
  }, [theme]);

  return <>{props.children}</>;
}
