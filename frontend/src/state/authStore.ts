/**
 * Bearer token 存取(会话 JWT / API token,README §6.14 鉴权)。
 * 供 MeshApiClient.getToken 与 RealtimeClient 子协议鉴权(§6.16)共用。
 * 骨架阶段 token 经登录占位页粘帖写入;阶段 2 接真实 auth 流程(auth.md)。
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface AuthState {
  token: string | null;
  setToken: (token: string | null) => void;
  clearToken: () => void;
}

export const AUTH_STORAGE_KEY = 'mesh.auth.v1';

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      setToken: (token) => set({ token }),
      clearToken: () => set({ token: null }),
    }),
    { name: AUTH_STORAGE_KEY },
  ),
);

/** 供非 React 上下文(客户端实例)读取当前 token */
export function getToken(): string | null {
  return useAuthStore.getState().token;
}
