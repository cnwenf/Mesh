/**
 * 首帧分区镜像键写入与登出清理(theme.md §2.3 ②)。
 *
 * `mesh.theme.active` 为单键 `{id: <route_id>, mode: 'light'|'dark'}`;
 * 偏好 store 在每次解析完成、登录、切换工作区后以当前路由身份**单键覆盖**
 * 回写;登出时清理 locator + 遗留镜像键(防下一账号串用)。
 *
 * 残留风险覆盖(§2.3):id 不含 user_id——由「登出清理 + 登录后首次解析
 * 必回写 + 正常导航走精确注入」三层兜底;镜像链路仅在静态缓存入口生效。
 */
import type { ResolvedTheme } from './themeNegotiation';
import { expectedRouteId } from './themeNegotiation';

export const THEME_LOCATOR_KEY = 'mesh.theme.active';
/** 阶段 2 遗留镜像键(仅存 mode 字符串,无分区身份)——登出清理时一并移除。 */
export const LEGACY_THEME_MIRROR_KEY = 'mesh.theme';

/**
 * 以当前路由身份回写 locator(单键覆盖)。
 * 存储不可用(隐私模式等)时静默降级——仅损失静态缓存入口的首帧精确性。
 */
export function writeThemeLocator(mode: ResolvedTheme, href: string = window.location.href): void {
  try {
    const payload = JSON.stringify({ id: expectedRouteId(href), mode });
    localStorage.setItem(THEME_LOCATOR_KEY, payload);
  } catch {
    /* 存储不可用:内存态与注入链路不受影响 */
  }
}

/**
 * 登出清理:删除 locator、遗留镜像键与历史分区格式残留键。
 * 幂等;存储不可用时静默降级。pending 偏好队列的主体隔离由队列模块自身的
 * 三元组校验保证(mesh.settings.pending:{host}:{user}:{workspace})。
 */
export function clearThemeLocators(): void {
  try {
    localStorage.removeItem(THEME_LOCATOR_KEY);
    localStorage.removeItem(LEGACY_THEME_MIRROR_KEY);
    const doomed: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key !== null && key.startsWith(`${THEME_LOCATOR_KEY}:`)) {
        doomed.push(key);
      }
    }
    for (const key of doomed) {
      localStorage.removeItem(key);
    }
  } catch {
    /* 存储不可用即无残留可言 */
  }
}
