/**
 * Bearer token 存取(会话 JWT / API token,README §6.14 鉴权)。
 * 供 MeshApiClient.getToken 与 RealtimeClient 首帧鉴权(§6.16)共用。
 *
 * auth.md §4.5 会话模型(R4-H1):`token` 为短期 access JWT(用于 Bearer),
 * 随登录写入、登出清除;refresh 仅存 HttpOnly cookie(`mesh_session`),JS 永不
 * 持有 refresh 明文——续期由浏览器自动携带 cookie 完成。沿用脚手架既定的持久化
 * 方案(zustand persist → localStorage,仅持久化 access)。
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { onLogoutCleanup } from './settingsStore';

export interface AuthState {
  /** 短期 access JWT(请求 Bearer 用) */
  token: string | null;
  setToken: (token: string | null) => void;
  /** 登录成功写入 access(R4-H1:refresh 仅存 HttpOnly cookie,JS 不持有) */
  setSession: (tokens: { accessToken: string }) => void;
  clearToken: () => void;
}

export const AUTH_STORAGE_KEY = 'mesh.auth.v1';

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      setToken: (token) => set({ token }),
      setSession: ({ accessToken }) => set({ token: accessToken }),
      clearToken: () => {
        // theme.md §2.3:登出清理分区 locator + 遗留镜像键,防下一账号串用;
        // 偏好回到「未表达」(协商链自工作区默认起)。
        onLogoutCleanup();
        set({ token: null });
      },
    }),
    { name: AUTH_STORAGE_KEY },
  ),
);

/** 供非 React 上下文(客户端实例)读取当前 access token */
export function getToken(): string | null {
  return useAuthStore.getState().token;
}
