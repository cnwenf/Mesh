import { afterEach, describe, expect, it } from 'vitest';
import { MeshApiClient } from '../client';
import { getApiClient, resetApiClient } from '../instance';

describe('API 客户端单例(instance.ts)', () => {
  afterEach(() => {
    resetApiClient();
  });

  it('getApiClient 返回 MeshApiClient 实例', () => {
    const client = getApiClient();
    expect(client).toBeInstanceOf(MeshApiClient);
  });

  it('getApiClient 多次调用返回同一实例(单例)', () => {
    const first = getApiClient();
    const second = getApiClient();
    expect(first).toBe(second);
  });

  it('resetApiClient 后重新创建新实例', () => {
    const first = getApiClient();
    resetApiClient();
    const second = getApiClient();
    expect(first).not.toBe(second);
    expect(second).toBeInstanceOf(MeshApiClient);
  });
});
