/**
 * 全局 API 客户端实例 — 组装 MeshApiClient(env + authStore token)。
 * 供 App 层 Provider 树与偏好同步模块共用。
 *
 * MES-106:onUnauthorized 接通 401 全局兜底(unauthorized.ts)——受保护端点
 * 的 401 不再由各组件各自呈现「加载失败」,统一清 token 并跳 /login?next=<原路径>。
 */
import { MeshApiClient } from './client';
import { getToken } from './tokenStore';
import { handleUnauthorized } from './unauthorized';
import { env } from '../env';

let instance: MeshApiClient | null = null;

/** 获取全局 API 客户端单例(懒初始化) */
export function getApiClient(): MeshApiClient {
  if (instance === null) {
    instance = new MeshApiClient({
      baseUrl: env.apiBaseUrl,
      getToken,
      onUnauthorized: () => {
        handleUnauthorized();
      },
    });
  }
  return instance;
}

/** 重置单例(仅测试用) */
export function resetApiClient(): void {
  instance = null;
}
