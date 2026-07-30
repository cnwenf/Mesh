/**
 * 401 全局兜底(MES-106)— 权威:auth.md §4.1 回跳守卫。
 *
 * 未登录/token 失效时,受保护接口回 401。若放任各组件各自呈现「加载失败」,
 * 用户无从得知需要登录(手机端实测:停在首页连收 401,满屏错误)。此模块把
 * 401 收敛为单一动作:清除本地 access token + 跳 `/login?next=<当前路径>`,
 * 登录后经 LoginPage 的 safeNextPath 守卫回跳原页(与 §4.1 既有 `?next=`
 * 契约、OAuth 往返共用同一守卫,参数名不漂移)。
 *
 * 鉴权豁免:登录/注册/MFA 验证/密码重置等「登录前可调用」端点的 4xx 属业务
 * 错误(如 422 invalid_credentials),由页面就地呈现具名文案,**不得**触发兜底
 * 跳转——否则登录失败会被劫持成整页重定向,错误态永远无法呈现。
 */
import { useAuthStore } from '../state/authStore';

/** 登录页路径(守卫跳转目标) */
export const LOGIN_PATH = '/login';

/** 登录前可调用、401 不触发全局兜底的端点(精确路径或前缀) */
const AUTH_EXEMPT_PATHS: readonly string[] = [
  '/api/v1/auth/login',
  '/api/v1/auth/register',
  '/api/v1/auth/mfa/verify',
  '/api/v1/auth/forgot-password',
  '/api/v1/auth/reset-password',
  '/api/v1/auth/verify-email',
  // OAuth 往返端点(start 302 / callback 交换):回调页有自己的失败 UI(§4.5)。
  '/api/v1/auth/oauth/',
];

/**
 * 判定请求路径是否鉴权豁免(登录前即可调用,401 为业务错误而非会话失效)。
 * 精确项全等匹配;以 `/` 结尾的项为前缀匹配。路径恒以 `/api/v1/` 起(客户端
 * 内部路径),无需考虑大小写或相对形态。
 */
export function isAuthExemptPath(path: string): boolean {
  return AUTH_EXEMPT_PATHS.some((exempt) =>
    exempt.endsWith('/') ? path.startsWith(exempt) : path === exempt,
  );
}

/** 由当前路径构造登录跳转 URL(`?next=` 携带原目标,登录态回跳;§4.1)。 */
export function buildLoginRedirectUrl(currentPath: string): string {
  return `${LOGIN_PATH}?next=${encodeURIComponent(currentPath)}`;
}

/**
 * 401 兜底动作:清除 access token(守卫随之生效)+ 跳登录页并携带当前路径。
 * 已在登录页时仅清 token 不跳转(登录失败页就地呈现;跳转会丢表单态且成环)。
 *
 * 副作用注入点:`redirect` 与 `location` 均可替身(单测不触真实导航);
 * 缺省经 window.location.assign 整页跳转——会话失效时丢弃全部客户端态
 * (在途请求 / WS 连接 / 过期缓存)恰是期望行为。
 */
export function handleUnauthorized(
  redirect: (url: string) => void = (url) => {
    window.location.assign(url);
  },
  location: Pick<Location, 'pathname' | 'search'> = window.location,
): void {
  useAuthStore.getState().clearToken();
  if (location.pathname.startsWith(LOGIN_PATH)) {
    return;
  }
  redirect(buildLoginRedirectUrl(location.pathname + location.search));
}
