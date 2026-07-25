import { describe, expect, it } from 'vitest';
import * as api from '../index';

describe('api 桶导出(README §6.14/§6.5 公共符号)', () => {
  it('导出错误/客户端/分页/乐观更新/过滤全部公共符号', () => {
    expect(api.MeshApiError).toBeTypeOf('function');
    expect(api.errorToI18nKey).toBeTypeOf('function');
    expect(api.MeshApiClient).toBeTypeOf('function');
    expect(api.useCursorPagination).toBeTypeOf('function');
    expect(api.fetchAllPages).toBeTypeOf('function');
    expect(api.optimisticUpdate).toBeTypeOf('function');
    expect(api.useOptimisticMutation).toBeTypeOf('function');
    expect(api.measureFilters).toBeTypeOf('function');
    expect(api.validateFilters).toBeTypeOf('function');
    expect(api.classifyFilterError).toBeTypeOf('function');
    expect(api.bearerHeader).toBeTypeOf('function');
    expect(api.getToken).toBeTypeOf('function');
    expect(api.useAuthStore).toBeTypeOf('function');
  });

  it('导出常量值正确', () => {
    expect(api.AUTH_HEADER).toBe('Authorization');
    expect(api.MAX_FILTER_DEPTH).toBe(3);
    expect(api.MAX_FILTER_CONDITIONS).toBe(20);
  });
});
