import { describe, expect, it } from 'vitest';
import { MeshApiError } from '../errors';
import {
  MAX_FILTER_CONDITIONS,
  MAX_FILTER_DEPTH,
  classifyFilterError,
  measureFilters,
  validateFilters,
} from '../filters';
import type { FilterCondition, FilterNode } from '../filters';

function cond(field: string): FilterCondition {
  return { field, op: 'eq', value: 'x' };
}

describe('filter 限制常量(README §6.14)', () => {
  it('深度上限 3、条件数上限 20', () => {
    expect(MAX_FILTER_DEPTH).toBe(3);
    expect(MAX_FILTER_CONDITIONS).toBe(20);
  });
});

describe('measureFilters(深度/条件数计量)', () => {
  it('裸条件:深度 1、条件数 1', () => {
    expect(measureFilters(cond('status'))).toEqual({ depth: 1, conditionCount: 1 });
  });

  it('单层分组 {and:[cond,cond]}:深度 2、条件数 2', () => {
    const node: FilterNode = { and: [cond('a'), cond('b')] };
    expect(measureFilters(node)).toEqual({ depth: 2, conditionCount: 2 });
  });

  it('嵌套分组逐层 +1 深度,条件数累加', () => {
    // depth: and(1) > or(2) > and(3) > cond(4)
    const node: FilterNode = {
      and: [cond('a'), { or: [cond('b'), { and: [cond('c'), cond('d')] }] }],
    };
    expect(measureFilters(node)).toEqual({ depth: 4, conditionCount: 4 });
  });

  it('深度取最深子树;or 与 and 等价处理', () => {
    const node: FilterNode = { or: [cond('a'), { or: [cond('b')] }] };
    expect(measureFilters(node)).toEqual({ depth: 3, conditionCount: 2 });
  });
});

describe('validateFilters(客户端预校验)', () => {
  it('合法过滤(深度 3、条件数 ≤20)通过', () => {
    const node: FilterNode = { and: [cond('a'), { or: [cond('b'), cond('c')] }] };
    expect(() => validateFilters(node)).not.toThrow();
  });

  it('深度 >3 抛 400 filter_too_complex,details 带 depth 与 max', () => {
    // Arrange: depth 4
    const node: FilterNode = { and: [{ or: [{ and: [cond('a')] }] }] };

    // Act / Assert
    try {
      validateFilters(node);
      expect.fail('应当抛出');
    } catch (err) {
      expect(err).toBeInstanceOf(MeshApiError);
      const apiErr = err as MeshApiError;
      expect(apiErr.status).toBe(400);
      expect(apiErr.code).toBe('filter_too_complex');
      expect(apiErr.details).toEqual({ depth: 4, max: MAX_FILTER_DEPTH });
    }
  });

  it('条件数 >20 抛 400 filter_too_complex,details 带 conditionCount 与 max', () => {
    // Arrange: 21 个条件,深度 2(不触发深度限制)
    const conditions = Array.from({ length: 21 }, (_, i) => cond(`f${i}`));
    const node: FilterNode = { and: conditions };

    // Act / Assert
    try {
      validateFilters(node);
      expect.fail('应当抛出');
    } catch (err) {
      const apiErr = err as MeshApiError;
      expect(apiErr.status).toBe(400);
      expect(apiErr.code).toBe('filter_too_complex');
      expect(apiErr.details).toEqual({ conditionCount: 21, max: MAX_FILTER_CONDITIONS });
    }
  });
});

describe('classifyFilterError(服务端错误归类)', () => {
  it('400 + filter_too_complex → filter_too_complex', () => {
    const err = new MeshApiError({ status: 400, code: 'filter_too_complex', message: 'x' });
    expect(classifyFilterError(err)).toBe('filter_too_complex');
  });

  it('422 + query_cost_exceeded → query_cost_exceeded', () => {
    const err = new MeshApiError({ status: 422, code: 'query_cost_exceeded', message: 'x' });
    expect(classifyFilterError(err)).toBe('query_cost_exceeded');
  });

  it('其它状态/代码 → null', () => {
    expect(
      classifyFilterError(new MeshApiError({ status: 400, code: 'validation_error', message: 'x' })),
    ).toBeNull();
    expect(
      classifyFilterError(new MeshApiError({ status: 422, code: 'filter_too_complex', message: 'x' })),
    ).toBeNull();
    expect(
      classifyFilterError(new MeshApiError({ status: 500, code: 'internal_error', message: 'x' })),
    ).toBeNull();
  });
});
