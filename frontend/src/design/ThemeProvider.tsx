/**
 * 主题切换契约(theme.md §2.2 协商链 + §2.3 首帧链路 + §4.2 即时生效)。
 *
 * 协商链(镜像 §6.18 locale 链):
 *   用户偏好(absent/null 跳过;显式 system 本级终止跟随 OS)
 *     → 工作区默认(workspaceThemeBridge,经 WorkspaceProvider 实时联动)
 *     → 系统(prefers-color-scheme 动态结果,实时跟随 T8)。
 *
 * 首帧链路:内联脚本已完成「注入 → 分区 locator → skeleton 标记」三级;
 * 本 Provider 挂载后以协商链权威解析覆盖——注入/locator 命中的首帧
 * (data-theme 已置且无 pending 标记)不闪 skeleton,其余在协商完成前
 * 只渲染中性 skeleton(宁可短暂无主题骨架,不可先错后改)。
 *
 * 每次解析完成:落 `<html data-theme>`、回写分区 locator、联动
 * `meta theme-color`(仅改 meta,不引入新取色路径,§4.2)。
 */
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useSettingsStore } from '../state/settingsStore';
import { useWorkspaceThemeBridge } from '../state/workspaceThemeBridge';
import { writeThemeLocator } from './themeLocator';
import { resolveThemeChain } from './themeNegotiation';
import type { ResolvedTheme } from './themeNegotiation';
import { DARK_TOKENS, LIGHT_TOKENS } from './tokenValues';
import { ThemeSkeleton } from './ThemeSkeleton';

const DARK_SCHEME_QUERY = '(prefers-color-scheme: dark)';
const PENDING_ATTR = 'data-theme-pending';

/** 兼容导出:将主题模式解析为实际应用的主题(system 跟随系统偏好)。 */
export function resolveTheme(
  mode: 'light' | 'dark' | 'system',
  systemPrefersDark: boolean,
): ResolvedTheme {
  return resolveThemeChain({
    userTheme: mode,
    workspaceDefault: null,
    systemPrefersDark,
  }).mode;
}

function firstFrameHasTheme(): boolean {
  const el = document.documentElement;
  return el.hasAttribute('data-theme') && !el.hasAttribute(PENDING_ATTR);
}

/**
 * meta theme-color 联动(§4.2):system 态由亮/暗双声明随 OS;显式切换
 * (user light/dark)时两条 meta 均改写为当前解析表面色(仅改 meta)。
 */
function syncMetaThemeColor(mode: ResolvedTheme, explicit: boolean): void {
  const metas = document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]');
  if (metas.length === 0) return;
  if (explicit) {
    const value = mode === 'dark' ? DARK_TOKENS['--color-bg'] : LIGHT_TOKENS['--color-bg'];
    metas.forEach((meta) => meta.setAttribute('content', value));
  } else {
    metas.forEach((meta) => {
      const media = meta.getAttribute('media') ?? '';
      if (media.includes('dark')) {
        meta.setAttribute('content', DARK_TOKENS['--color-bg']);
      } else if (media.includes('light')) {
        meta.setAttribute('content', LIGHT_TOKENS['--color-bg']);
      }
    });
  }
}

export function ThemeProvider(props: { children: ReactNode }): React.JSX.Element {
  const userTheme = useSettingsStore((state) => state.preferences.theme);
  const workspaceDefault = useWorkspaceThemeBridge((state) => state.defaultTheme);
  const workspaceLoaded = useWorkspaceThemeBridge((state) => state.loaded);
  const [systemPrefersDark, setSystemPrefersDark] = useState(
    () => window.matchMedia(DARK_SCHEME_QUERY).matches,
  );
  // 内联脚本已精确注入/命中 locator 的首帧:抑制挂载瞬间的 skeleton 闪烁。
  const firstFrameHint = useRef(firstFrameHasTheme());

  const { mode } = resolveThemeChain({
    userTheme,
    workspaceDefault,
    systemPrefersDark,
  });

  const pending = userTheme === null && !workspaceLoaded && !firstFrameHint.current;

  // system 偏好实时跟随(T8):matchMedia change 监听,卸载注销。
  useEffect(() => {
    const media = window.matchMedia(DARK_SCHEME_QUERY);
    const handleChange = (event: MediaQueryListEvent): void => {
      setSystemPrefersDark(event.matches);
    };
    media.addEventListener('change', handleChange);
    return () => media.removeEventListener('change', handleChange);
  }, []);

  // 权威解析落地:切换即时无刷新(仅改 <html data-theme>,§4.2 T6)。
  useEffect(() => {
    if (pending) return;
    const el = document.documentElement;
    el.dataset.theme = mode;
    el.removeAttribute(PENDING_ATTR);
    writeThemeLocator(mode);
    syncMetaThemeColor(mode, userTheme === 'light' || userTheme === 'dark');
    firstFrameHint.current = false;
  }, [mode, pending, userTheme]);

  // pending 期间以中性 skeleton 覆盖视口(§2.3 ③:不呈现业务内容),但 children
  // 保持挂载——其中恰含供给工作区默认的 WorkspaceProvider,卸载会死锁协商链。
  // 非 pending 时包裹层 display:contents,布局中性(无生成盒)。
  return (
    <>
      <div
        data-testid="theme-children-root"
        style={pending ? { display: 'none' } : { display: 'contents' }}
      >
        {props.children}
      </div>
      {pending ? <ThemeSkeleton /> : null}
    </>
  );
}
