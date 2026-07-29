/**
 * 工作区默认主题桥接(theme.md §2.2 协商链第 2 级)。
 *
 * ThemeProvider 挂载于路由树根部(WorkspaceProvider 之外),无法直接消费
 * 工作区上下文;WorkspaceProvider 加载/实时更新工作区 settings 后经本桥接
 * 写入 `default_theme`,ThemeProvider 读取并解析协商链。
 *
 * 语义:
 * - 初值 `{defaultTheme: null, loaded: false}`:乐观假定「可能在工作区路由」,
 *   配合 ThemeProvider 的首帧保持(H3)消除全局/匿名首帧的 light→dark 翻转闪烁;
 *   全局/匿名页由 bootstrap 的 `markSessionProbed` 显式置 loaded=true 解锁,
 *   工作区路由由 WorkspaceProvider 在 detail 就绪后置 loaded=true;
 * - 进入工作区路由:WorkspaceProvider 挂载即 `beginWorkspaceLoad`(loaded=false),
 *   detail 就绪后写入 `default_theme` 并 loaded=true;
 * - `workspace.updated` 实时事件由 WorkspaceProvider 浅合并进 settings,
 *   依赖该对象的桥接写入随之刷新 → 未设显式偏好的成员即时重解析(§4.5)。
 *
 * 匿名防死锁:ThemeProvider 的 skeleton 仅当 `sessionProbed`(bootstrap 已完成)
 * 才可能触发;匿名无 session 时 bootstrap 不置 probed → 匿名永不陷 skeleton。
 */
import { create } from 'zustand';
import { isThemeMode } from '../design/themeNegotiation';
import type { ThemeMode } from '../design/themeNegotiation';

export interface WorkspaceThemeBridgeState {
  /** 当前工作区 `settings.default_theme`(白名单收敛;null = 未设/系统) */
  defaultTheme: ThemeMode | null;
  /** 工作区默认级是否已就绪(false = 期望本级解析但尚在加载 → skeleton) */
  loaded: boolean;
  /** 接受未校验的服务端字符串;白名单收敛,非法值 → null。 */
  setWorkspaceDefault(theme: string | null | undefined): void;
  /** 工作区上下文挂载:期望本级解析,标记未就绪。 */
  beginWorkspaceLoad(): void;
  /** 工作区上下文卸载:回到「无工作区上下文」(loaded 恢复 true)。 */
  endWorkspaceContext(): void;
}

export const useWorkspaceThemeBridge = create<WorkspaceThemeBridgeState>()((set) => ({
  defaultTheme: null,
  loaded: false,

  setWorkspaceDefault: (theme) =>
    set({ defaultTheme: isThemeMode(theme) ? theme : null, loaded: true }),

  beginWorkspaceLoad: () => set({ loaded: false }),

  endWorkspaceContext: () => set({ defaultTheme: null, loaded: true }),
}));

/** 供非工作区上下文的公开页(邀请接受页)写入预览解析出的工作区默认。 */
export function setWorkspaceDefaultFromPreview(theme: string | null | undefined): void {
  useWorkspaceThemeBridge.getState().setWorkspaceDefault(theme);
}

/** 进入工作区/邀请入口:标记「期望本级解析但未就绪」(skeleton 兜底)。 */
export function beginWorkspaceLoad(): void {
  useWorkspaceThemeBridge.getState().beginWorkspaceLoad();
}

/** 离开工作区/邀请入口:回到「无工作区上下文」(loaded 恢复 true)。 */
export function endWorkspaceContext(): void {
  useWorkspaceThemeBridge.getState().endWorkspaceContext();
}
