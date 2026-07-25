/**
 * 全局 API 客户端实例 — 组装 MeshApiClient(env + authStore token)。
 * 供 App 层 Provider 树与偏好同步模块共用。
 */
import { MeshApiClient } from './client';
import { getToken } from './tokenStore';
import { env } from '../env';

let instance: MeshApiClient | null = null;

/** 获取全局 API 客户端单例(懒初始化) */
export function getApiClient(): MeshApiClient {
  if (instance === null) {
    instance = new MeshApiClient({
      baseUrl: env.apiBaseUrl,
      getToken,
    });
  }
  return instance;
}

/** 重置单例(仅测试用) */
export function resetApiClient(): void {
  instance = null;
}
