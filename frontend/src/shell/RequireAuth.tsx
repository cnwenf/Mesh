/**
 * 路由守卫(MES-106)— 权威:auth.md §4.1 回跳守卫。
 *
 * 未登录(无 access token)访问受保护页面时,不渲染子树、不发起受保护请求,
 * 直接 `Navigate` 到 `/login?next=<原路径(含查询串)>`;登录成功经 LoginPage
 * 的 safeNextPath 守卫回跳原页(与 OAuth 往返共用同一 `?next=` 契约)。
 *
 * 用法:AppShell 布局内以 pathless layout route 包裹受保护子路由;公开页
 * (如邀请接受页 preview,未登录可见)留在守卫之外,与守卫同级嵌套。
 *
 * token 存在但失效(过期/被撤销)的情形由 API 层 401 全局兜底收口
 * (api/unauthorized.ts 清 token → 本守卫随之触发跳转),二者不重叠:
 * 守卫只判「本地无 token」,绝不自行发探测请求。
 */
import { Navigate, Outlet, useLocation } from 'react-router';
import { LOGIN_PATH } from '../api/unauthorized';
import { useAuthStore } from '../state/authStore';

export function RequireAuth(): React.JSX.Element {
  const hasToken = useAuthStore((state) => state.token !== null);
  const location = useLocation();
  if (!hasToken) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`${LOGIN_PATH}?next=${next}`} replace />;
  }
  return <Outlet />;
}
