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
import { ThemeProvider as AppicaThemeProvider } from '@appica/ui-react/providers/theme-provider';
import { useSettingsStore } from '../state/settingsStore';
import { useWorkspaceThemeBridge } from '../state/workspaceThemeBridge';
import { writeThemeLocator } from './themeLocator';
import {
  resolveThemeChain,
  routeExpectsWorkspaceDefault,
  ROUTE_CHANGE_EVENT,
} from './themeNegotiation';
import type { ResolvedTheme } from './themeNegotiation';
import { DARK_TOKENS, LIGHT_TOKENS } from './tokenValues';
import { THEME_CHANGED_EVENT } from './ugcColorGuard';
import { ThemeSkeleton } from './ThemeSkeleton';

const DARK_SCHEME_QUERY = '(prefers-color-scheme: dark)';
const PENDING_ATTR = 'data-theme-pending';
const APPICA_THEMES = ['light', 'dark'];
const INERT_APPICA_SCRIPT_PROPS = {
  type: 'application/json',
  'data-mesh-theme-bridge': '',
};

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

function readAppliedTheme(): ResolvedTheme | null {
  const value = document.documentElement.dataset.theme;
  return value === 'light' || value === 'dark' ? value : null;
}

/** Appica 的 dark variant 读取 class；Mesh 的服务端/业务契约读取 data-theme。 */
function syncThemeClass(el: HTMLElement, mode: ResolvedTheme): void {
  el.classList.remove('light', 'dark');
  el.classList.add(mode);
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
  const sessionProbed = useSettingsStore((state) => state.sessionProbed);
  const workspaceDefault = useWorkspaceThemeBridge((state) => state.defaultTheme);
  const workspaceLoaded = useWorkspaceThemeBridge((state) => state.loaded);
  const [systemPrefersDark, setSystemPrefersDark] = useState(
    () => window.matchMedia(DARK_SCHEME_QUERY).matches,
  );
  // 当前路由是否期望工作区默认(H3):随 SPA 导航更新,决定协商链是否可信。
  const [routeExpectsWs, setRouteExpectsWs] = useState(() =>
    routeExpectsWorkspaceDefault(window.location.href),
  );
  // 内联脚本已精确注入/命中 locator 的首帧:权威数据,首帧不可被链覆盖。
  const firstFrameHint = useRef(firstFrameHasTheme());

  const { mode } = resolveThemeChain({
    userTheme,
    workspaceDefault,
    systemPrefersDark,
  });

  // 协商链是否可信解析(H3):用户已显式选择 / 路由不期望工作区默认 / 工作区
  // 桥接已就绪。否则(工作区路由且桥接未就绪)链不可信——若首帧已有注入/locator
  // 提示则**保持**该首帧(不覆盖、不写 locator),否则呈现中性 skeleton。
  const chainReady = userTheme !== null || !routeExpectsWs || workspaceLoaded;
  // skeleton 仅在「链不可信 + 无首帧提示 + bootstrap 已探测会话」时呈现:
  // - 匿名(无 session)→ bootstrap 不置 sessionProbed → 永不陷 skeleton;
  // - 注入/locator 首帧(firstFrameHint)→ 保持首帧,不 skeleton;
  // - 仅「已登录 + 工作区路由 + 桥接未就绪 + 无任何首帧提示」这一窄窗口才 skeleton。
  const pending = !chainReady && !firstFrameHint.current && sessionProbed;
  // 链尚未可信时沿用服务端注入/locator 的首帧值；无提示时业务内容被 skeleton
  // 覆盖，临时 mode 不可见。Appica 只消费该解析结果，不拥有偏好存储。
  const appicaMode = chainReady ? mode : (readAppliedTheme() ?? mode);

  // system 偏好实时跟随(T8):matchMedia change 监听,卸载注销。
  useEffect(() => {
    const media = window.matchMedia(DARK_SCHEME_QUERY);
    const handleChange = (event: MediaQueryListEvent): void => {
      setSystemPrefersDark(event.matches);
    };
    media.addEventListener('change', handleChange);
    return () => media.removeEventListener('change', handleChange);
  }, []);

  // 路由身份变更(popstate + 客户端导航补丁)→ 重算是否期望工作区默认。
  useEffect(() => {
    const update = (): void =>
      setRouteExpectsWs(routeExpectsWorkspaceDefault(window.location.href));
    window.addEventListener('popstate', update);
    window.addEventListener(ROUTE_CHANGE_EVENT, update);
    return () => {
      window.removeEventListener('popstate', update);
      window.removeEventListener(ROUTE_CHANGE_EVENT, update);
    };
  }, []);

  // 权威解析落地(H3):链不可信但有首帧提示时「保持」首帧(注入/locator 已是
  // 服务端按当前路由协商的权威结果,链尚未就绪不可覆盖,避免错误闪烁);链不可信
  // 且无提示时维持 skeleton;链就绪时应用链解析值并回写 locator。
  useEffect(() => {
    const el = document.documentElement;
    if (!chainReady) {
      if (firstFrameHint.current) {
        const firstFrameMode = readAppliedTheme();
        if (firstFrameMode !== null) syncThemeClass(el, firstFrameMode);
        el.removeAttribute(PENDING_ATTR); // 首帧提示已绘制,清 pending 标记
      }
      return;
    }
    el.dataset.theme = mode;
    syncThemeClass(el, mode);
    el.removeAttribute(PENDING_ATTR);
    writeThemeLocator(mode);
    syncMetaThemeColor(mode, userTheme === 'light' || userTheme === 'dark');
    // UGC 内联色兜底等主题相关后处理的重扫信号(§4.3 T5③)。
    window.dispatchEvent(new CustomEvent(THEME_CHANGED_EVENT));
    firstFrameHint.current = false;
  }, [mode, chainReady, userTheme]);

  // pending 期间以中性 skeleton 覆盖视口(§2.3 ③:不呈现业务内容),但 children
  // 保持挂载——其中恰含供给工作区默认的 WorkspaceProvider,卸载会死锁协商链。
  // 非 pending 时包裹层 display:contents,布局中性(无生成盒)。
  return (
    <AppicaThemeProvider
      forcedTheme={appicaMode}
      defaultTheme={appicaMode}
      themes={APPICA_THEMES}
      enableSystem={false}
      enableColorScheme={false}
      storageKey=""
      // Mesh 已在 index.html 以 CSP-safe 脚本完成首帧协商；把库自带脚本设为
      // 惰性数据节点，避免第二条脚本/存储链与服务端注入竞争。
      scriptProps={INERT_APPICA_SCRIPT_PROPS}
    >
      <div
        data-testid="theme-children-root"
        style={pending ? { display: 'none' } : { display: 'contents' }}
      >
        {props.children}
      </div>
      {pending ? <ThemeSkeleton /> : null}
    </AppicaThemeProvider>
  );
}
